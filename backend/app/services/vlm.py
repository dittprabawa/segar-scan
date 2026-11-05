"""Penilai kesegaran lewat model vision (Gemini). Dipakai untuk demo."""

import io
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutTimeout
from typing import Optional

from PIL import Image

# Naikkan versi ini setiap kali isi PROMPT diubah.
PROMPT_VERSION = "p2"

PROMPT = """Kamu menilai kesegaran SATU jenis sayuran dari foto, untuk konsumen di pasar.
Balas HANYA JSON dengan kunci persis ini:
{"valid": true atau false,
 "alasan_tolak": "isi jika valid=false, bahasa Indonesia sehari-hari, satu kalimat",
 "jenis": "nama sayur huruf kecil, mis. bayam, tomat, kangkung",
 "skor": angka 0-100,
 "skor_min": angka 0-100,
 "skor_maks": angka 0-100,
 "keyakinan": "rendah" atau "sedang" atau "tinggi",
 "ciri": "satu kalimat bahasa Indonesia sehari-hari tentang ciri yang terlihat"}

Aturan:
- Tentukan jenis sebelum menilai kesegaran. Periksa bentuk dan tepi daun, tulang daun, tangkai, batang, serta susunannya yang benar-benar terlihat. Warna hijau atau batang pucat saja tidak cukup untuk menentukan jenis.
- Untuk jenis yang mirip seperti bayam, caisim, dan pakcoy, bandingkan beberapa ciri yang terlihat. Jangan langsung memilih caisim hanya karena sayur berdaun hijau dan bertangkai panjang; jangan pula memaksakan bayam tanpa bukti visual.
- Jika ciri pembeda tidak terlihat atau jenis masih ambigu, balas valid=false dan minta foto ulang yang menampilkan daun serta batang dengan jelas. Jangan menebak jenis atau memberikan skor kesegaran saat identifikasi belum jelas.
- valid=false jika gambar bukan sayuran, gelap, buram, atau berisi beberapa jenis sayur campur. Jangan beri skor jika valid=false.
- valid=false jika pencahayaan terlalu terang atau silau sehingga warna asli, tekstur, atau bercak pada sayuran tidak bisa dinilai. Sayuran yang masih bisa dikenali belum tentu bisa dinilai kesegarannya. Latar putih saja bukan alasan menolak jika detail sayur tetap jelas.
- Skor: 80-100 sangat segar, 60-79 segar, 40-59 segera olah, 20-39 layu, 0-19 tidak layak.
- Nilai dari warna, kelayuan, bercak/lendir, kilap, dan kerusakan fisik.
- Rentang skor_min sampai skor_maks harus mencerminkan ketidakpastianmu (lebar 10-20 poin).
- Sayur yang basah atau tampak disemprot air bisa terlihat lebih segar daripada aslinya: turunkan keyakinan.
- Ini perkiraan visual. Jangan menyebut aman dimakan, bebas pestisida, atau pasti segar.
"""

log = logging.getLogger("segarscan")

_client = None
_client_lock = threading.Lock()

# Batas panggilan Gemini yang berjalan bersamaan (semua permintaan digabung, per proses).
_MAKS_BERSAMAAN = max(1, int(os.getenv("VLM_MAX_BERSAMAAN", "3")))
_slot = threading.BoundedSemaphore(_MAKS_BERSAMAAN)

# Model yang kena 429 diistirahatkan sebentar: {nama_model: waktu_monotonic_selesai_istirahat}
_dingin: dict = {}
_dingin_lock = threading.Lock()


class _Sibuk(Exception):
    """Semua slot panggilan Gemini sedang terpakai."""


def tersedia() -> bool:
    return bool(os.getenv("GEMINI_API_KEY")) and os.getenv("USE_VLM", "1") == "1"


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from google import genai

            _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        return _client


def _bersihkan(teks: str) -> dict:
    teks = teks.strip()
    if teks.startswith("```"):
        teks = teks.strip("`").removeprefix("json").strip()
    return json.loads(teks)


def _angka(v, default: Optional[float] = None) -> Optional[float]:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return default


def _daftar_model() -> list:
    """GEMINI_MODEL boleh berisi beberapa nama dipisah koma; semuanya dipanggil bersamaan."""
    s = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite,gemini-3.8-flash")
    return [m.strip() for m in s.split(",") if m.strip()]


def _kena_429(e: Exception) -> bool:
    """True bila error berarti kuota/batas laju terlampaui (HTTP 429)."""
    for atribut in ("code", "status_code"):
        v = getattr(e, atribut, None)
        if isinstance(v, int):
            return v == 429
    s = str(e)
    return "RESOURCE_EXHAUSTED" in s or re.search(r"\b429\b", s) is not None


def _sedang_dingin(model: str) -> bool:
    with _dingin_lock:
        return _dingin.get(model, 0.0) > time.monotonic()


def _tandai_dingin(model: str) -> None:
    detik = float(os.getenv("VLM_COOLDOWN_429", "30"))
    with _dingin_lock:
        _dingin[model] = time.monotonic() + detik
    log.warning("Gemini %s kena 429, diistirahatkan %.0f detik", model, detik)


def _jawaban_sibuk() -> dict:
    """Jawaban ringkas bila Gemini sedang penuh atau kuota habis."""
    return {
        "valid": False,
        "sibuk": True,
        "alasan": "Penilai sedang ramai. Coba kirim ulang fotonya beberapa detik lagi.",
    }


def batas_waktu(deadline: Optional[float], per_model: float, minimum: float = 3.0) -> Optional[float]:
    """Detik yang boleh dipakai model, atau None bila sisa waktu terlalu sedikit.

    deadline  : waktu monotonic batas akhir seluruh permintaan (None = tanpa batas total)
    per_model : batas per model (VLM_TIMEOUT)
    """
    if deadline is None:
        return per_model
    sisa = deadline - time.monotonic()
    if sisa < minimum:
        return None
    return min(per_model, sisa)


def _opsi_http(types, detik: float):
    """Batas waktu per model dan tanpa ulangan otomatis."""
    ms = int(detik * 1000)
    try:
        return types.HttpOptions(timeout=ms, retry_options=types.HttpRetryOptions(attempts=1))
    except Exception:  # versi SDK lama tanpa retry_options
        return types.HttpOptions(timeout=ms)


def _panggil_model(model: str, isi, types, detik: float, tunggu_slot: float) -> dict:
    """Satu panggilan ke satu model. Dijalankan di thread terpisah."""
    if not _slot.acquire(timeout=tunggu_slot):
        raise _Sibuk(f"slot penuh ({_MAKS_BERSAMAAN} panggilan bersamaan)")
    try:
        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            # Jawaban JSON itu pendek; batas ini mencegahnya melebar. Jangan terlalu kecil:
            # pada model yang "berpikir", token berpikir ikut terhitung dan JSON bisa terpotong.
            max_output_tokens=int(os.getenv("VLM_MAX_TOKEN_JAWABAN", "1024")),
            http_options=_opsi_http(types, detik),
        )
        resp = _get_client().models.generate_content(model=model, contents=isi, config=config)
        d = _bersihkan(resp.text)
        if d.get("valid", False) and _angka(d.get("skor")) is None:
            raise ValueError("Respons tanpa skor")  # dianggap gagal, tunggu model lain
        d["_model"] = model
        return d
    except Exception as e:
        if _kena_429(e):
            _tandai_dingin(model)
        raise
    finally:
        _slot.release()


def nilai(image: Image.Image, deadline: Optional[float] = None) -> dict:
    """Semua model dipanggil bersamaan; hasil sukses tercepat yang dipakai.

    - Panggilan Gemini bersamaan dibatasi VLM_MAX_BERSAMAAN (default 3).
    - Model yang kena 429 diistirahatkan; model lain tetap dipakai.
    - Jika semua model penuh/429, mengembalikan jawaban ringkas
      {"valid": False, "sibuk": True, "alasan": ...} (tidak melempar error).
    - Error lain (timeout, JSON rusak, dll.) yang menimpa semua model tetap dilempar.
    """
    from google.genai import types

    semua = _daftar_model()
    if not semua:
        raise RuntimeError("Tidak ada model Gemini yang dikonfigurasi")

    models = [m for m in semua if not _sedang_dingin(m)]
    if not models:
        log.warning("Semua model sedang diistirahatkan karena 429")
        return _jawaban_sibuk()

    # Gambar dikecilkan agar token lebih hemat dan respons lebih cepat.
    # 768 px = satu potongan gambar di Gemini (biasanya ~258 token).
    ukuran = int(os.getenv("VLM_UKURAN_GAMBAR", "768"))
    kualitas = int(os.getenv("VLM_KUALITAS_JPEG", "82"))
    img = image.convert("RGB")  # JPEG tidak mendukung transparansi (RGBA/P); hasilnya salinan
    img.thumbnail((ukuran, ukuran))  # hanya mengecilkan, tidak pernah memperbesar
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=kualitas)
    isi = [types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), PROMPT]
    per_model = float(os.getenv("VLM_TIMEOUT", "20"))
    tunggu_slot = float(os.getenv("VLM_TUNGGU_SLOT", "5"))

    detik = batas_waktu(deadline, per_model)
    if detik is None:
        raise TimeoutError("batas waktu total habis")

    # Jangan pakai "with": blok with akan menunggu thread yang lambat selesai.
    pool = ThreadPoolExecutor(max_workers=len(models))
    futures = {pool.submit(_panggil_model, m, isi, types, detik, tunggu_slot): m for m in models}
    d = None
    terakhir = None
    sibuk = False
    try:
        for f in as_completed(futures, timeout=detik + tunggu_slot + 2):
            try:
                d = f.result()
                break  # yang pertama berhasil menang
            except _Sibuk as e:
                log.warning("Gemini %s dilewati: %s", futures[f], e)
                sibuk = True
                terakhir = e
            except Exception as e:  # 503 padat, 429 kuota, timeout, JSON rusak, dll.
                if _kena_429(e):
                    log.warning("Gemini %s kena 429, menunggu model lain", futures[f])
                    sibuk = True
                else:
                    log.exception("Gemini %s gagal: %s", futures[f], str(e)[:200])
                terakhir = e
    except FutTimeout:
        terakhir = terakhir or TimeoutError("semua model melewati batas waktu")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if d is None:
        if sibuk:
            return _jawaban_sibuk()
        raise terakhir or RuntimeError("Semua model gagal")

    if not d.get("valid", False):
        return {"valid": False, "alasan": d.get("alasan_tolak") or "Foto belum bisa dinilai."}

    skor = _angka(d.get("skor"))
    keyakinan = str(d.get("keyakinan", "sedang")).lower()
    if keyakinan not in ("rendah", "sedang", "tinggi"):
        keyakinan = "sedang"
    return {
        "valid": True,
        "jenis": str(d.get("jenis") or "unknown").lower(),
        "skor": skor,
        "skor_min": _angka(d.get("skor_min"), max(0.0, skor - 8)),
        "skor_maks": _angka(d.get("skor_maks"), min(100.0, skor + 8)),
        "keyakinan": keyakinan,
        "ciri": str(d.get("ciri") or ""),
        "model": d.get("_model"),
    }
