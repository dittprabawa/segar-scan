"""Penilai kesegaran lewat model vision (Gemini). Dipakai untuk demo."""

import io
import json
import logging
import os
import threading
import time
from typing import Optional

from PIL import Image

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
- valid=false jika gambar bukan sayuran, gelap, buram, atau berisi beberapa jenis sayur campur. Jangan beri skor jika valid=false.
- Skor: 80-100 sangat segar, 60-79 segar, 40-59 segera olah, 20-39 layu, 0-19 tidak layak.
- Nilai dari warna, kelayuan, bercak/lendir, kilap, dan kerusakan fisik.
- Rentang skor_min sampai skor_maks harus mencerminkan ketidakpastianmu (lebar 10-20 poin).
- Sayur yang basah atau tampak disemprot air bisa terlihat lebih segar daripada aslinya: turunkan keyakinan.
- Ini perkiraan visual. Jangan menyebut aman dimakan, bebas pestisida, atau pasti segar.
"""

log = logging.getLogger("segarscan")

_client = None
_client_lock = threading.Lock()


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
    """GEMINI_MODEL boleh berisi beberapa nama dipisah koma; dicoba berurutan."""
    s = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite,gemini-3.8-flash")
    return [m.strip() for m in s.split(",") if m.strip()]


def batas_waktu(deadline: Optional[float], per_model: float, minimum: float = 3.0) -> Optional[float]:
    """Detik yang boleh dipakai model berikutnya, atau None bila sisa waktu terlalu sedikit.

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
    """Batas waktu per model dan tanpa ulangan otomatis (agar cepat pindah ke model lain)."""
    ms = int(detik * 1000)
    try:
        return types.HttpOptions(timeout=ms, retry_options=types.HttpRetryOptions(attempts=1))
    except Exception:  # versi SDK lama tanpa retry_options
        return types.HttpOptions(timeout=ms)


def nilai(image: Image.Image, deadline: Optional[float] = None) -> dict:
    """Kembalikan dict bersih. Melempar exception jika semua model gagal atau waktu habis."""
    from google.genai import types

    img = image.copy()
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    isi = [types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), PROMPT]
    per_model = float(os.getenv("VLM_TIMEOUT", "20"))

    d = {}
    terakhir = None
    for model in _daftar_model():
        detik = batas_waktu(deadline, per_model)
        if detik is None:
            log.warning("Batas waktu total habis sebelum mencoba %s", model)
            terakhir = terakhir or TimeoutError("batas waktu total habis")
            break
        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            http_options=_opsi_http(types, detik),
        )
        try:
            resp = _get_client().models.generate_content(model=model, contents=isi, config=config)
            d = _bersihkan(resp.text)
            d["_model"] = model
            break
        except Exception as e:  # 503 padat, 429 kuota, timeout, JSON rusak, dll.
            log.exception("Gemini %s gagal: %s", model, str(e)[:200])
            terakhir = e
    if "_model" not in d:
        raise terakhir if terakhir else RuntimeError("Tidak ada model Gemini yang dikonfigurasi")

    if not d.get("valid", False):
        return {"valid": False, "alasan": d.get("alasan_tolak") or "Foto belum bisa dinilai."}

    skor = _angka(d.get("skor"))
    if skor is None:
        raise ValueError("Respons tanpa skor")
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
