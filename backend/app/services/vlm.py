"""Penilai kesegaran lewat model vision (Gemini). Dipakai untuk demo."""

import io
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutTimeout
from typing import Optional

from PIL import Image

# Naikkan versi ini setiap kali isi PROMPT diubah.
PROMPT_VERSION = "p5"

PROMPT = """Kamu menilai kesegaran SATU jenis bahan dapur (sayuran atau daging mentah) dari foto, untuk konsumen di pasar.
Balas HANYA JSON dengan kunci persis ini:
{"valid": true atau false,
 "alasan_tolak": "isi jika valid=false, bahasa Indonesia sehari-hari, satu kalimat",
 "jenis": "nama bahan huruf kecil, mis. bayam, wortel, daging ayam, daging sapi, daging kambing",
 "kategori": "sayuran" atau "daging",
 "skor": angka 0-100,
 "skor_min": angka 0-100,
 "skor_maks": angka 0-100,
 "keyakinan": "rendah" atau "sedang" atau "tinggi",
 "ciri": "satu kalimat bahasa Indonesia sehari-hari tentang ciri yang terlihat",
 "pembusukan": "jelas" atau "meragukan" atau "tidak_terlihat"}

Aturan:
- Tentukan kategori dan jenis sebelum menilai. Untuk sayuran, periksa bentuk dan tepi daun, tulang daun, tangkai, batang, serta susunannya yang benar-benar terlihat. Warna hijau atau batang pucat saja tidak cukup untuk menentukan jenis.
- Untuk jenis yang mirip seperti bayam, caisim, dan pakcoy, bandingkan beberapa ciri yang terlihat. Jangan langsung memilih caisim hanya karena sayur berdaun hijau dan bertangkai panjang; jangan pula memaksakan bayam tanpa bukti visual.
- Jika ciri pembeda tidak terlihat atau jenis masih ambigu, balas valid=false dan minta foto ulang yang menampilkan ciri bahan dengan jelas. Jangan menebak jenis atau memberikan skor kesegaran saat identifikasi belum jelas.
- Daging mentah termasuk cakupan: ayam, sapi, kambing, dan daging lain yang dapat diidentifikasi. Gunakan kategori=daging dan awalan jenis "daging". Jangan memaksakan identifikasi spesies dari warna saja; jika spesies tidak jelas, tulis jenis="daging (jenis belum pasti)" dan keyakinan=rendah.
- Untuk daging, jelaskan kondisi permukaan dan kerusakan yang terlihat. Warna saja tidak cukup untuk menyimpulkan pembusukan atau kesegaran. Jangan menyimpulkan aroma, suhu, lama penyimpanan, bakteri, kehalalan, atau keamanan konsumsi dari gambar. Skor hanya perkiraan kondisi visual.
- Jika daging tertutup kemasan tidak tembus pandang, tertutup es tebal, atau permukaannya tidak dapat diperiksa, valid=false dan minta foto yang memperlihatkan permukaan.
- valid=false jika gambar di luar sayuran/daging mentah, berupa masakan atau daging olahan, gelap, buram, atau berisi beberapa jenis bahan campur. Jangan beri skor jika valid=false.
- Untuk sayuran, skor: 80-100 sangat segar, 60-79 segar, 40-59 segera olah, 20-39 layu, 0-19 tidak layak.
- Untuk daging, skor 0-100 menggambarkan kondisi visual, bukan probabilitas aman atau kesegaran terukur; jangan memakai istilah layu atau menyarankan segera dimasak untuk mengatasi kerusakan.
- Periksa tanda pembusukan SEBELUM memberi skor: jelaskan lokasi dan bentuk kerusakan yang benar-benar terlihat. Jangan menilai hanya dari warna cerah atau kilap.
- Untuk wortel, periksa ujung, pangkal, dan permukaan tiap wortel yang terlihat: area gelap yang tampak rusak/cekung, jaringan hancur atau basah membusuk, dan pertumbuhan menyerupai jamur. Beberapa area kulit yang menghitam dan tampak cekung pada satu atau beberapa wortel merupakan bukti visual kuat pembusukan; tandai pembusukan=jelas dan skor 0-19 meskipun bagian lain masih oranye atau wortel berada dalam jaring plastik. Jangan mengabaikan kerusakan karena sebagian besar permukaan masih oranye.
- pembusukan=jelas jika terdapat bukti visual kuat pembusukan pada bagian mana pun yang terlihat. Beri skor serta seluruh rentang 0-19. Bahan busuk tetap valid=true jika jenis dan kerusakannya jelas: valid berarti foto dapat dinilai, bukan sayurnya segar.
- Bercak hitam tunggal tanpa cekungan atau kerusakan jaringan yang terlihat tidak membuktikan busuk: tanah, bayangan, dan luka kering dapat terlihat mirip. Jika hanya ada bercak tunggal yang tidak dapat dibedakan, pembusukan=meragukan; jelaskan ketidakpastiannya dan minta foto dekat area tersebut, jangan menyimpulkan segar. Untuk beberapa area menghitam yang jelas tampak cekung/rusak pada wortel, gunakan aturan wortel di atas, bukan meragukan.
- pembusukan=tidak_terlihat hanya jika bagian yang terlihat tidak menunjukkan tanda pembusukan atau bercak mencurigakan; ini bukan jaminan seluruh bahan bebas pembusukan.
- Beberapa buah, potongan daging, atau ikat dari SATU jenis diperbolehkan. Periksa kondisi yang bervariasi; jangan merata-ratakan bagian busuk dengan bagian segar sehingga tumpukan dinilai segar. Jelaskan jika kerusakan hanya terlihat pada sebagian objek. Bagian tertutup tidak boleh diasumsikan segar.
- Nilai juga kelayuan dan kerusakan fisik. Jangan menyimpulkan aroma, tekstur saat disentuh, atau kondisi bagian dalam dari foto.
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

# API Gemini menolak deadline HTTP di bawah 10 detik (400 INVALID_ARGUMENT). Timeout HTTP
# tiap panggilan tidak boleh lebih kecil dari ini; batas total permintaan tetap dijaga
# oleh loop di nilai(), bukan oleh timeout HTTP.
_DEADLINE_MIN_API = float(os.getenv("VLM_DEADLINE_MIN_API", "10"))

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
    d = json.loads(teks)
    if isinstance(d, list):  # model kadang membungkus jawaban: [{...}]
        d = next((x for x in d if isinstance(x, dict)), None)
    if not isinstance(d, dict):
        raise ValueError("Respons bukan objek JSON")
    return d


def _angka(v, default: Optional[float] = None) -> Optional[float]:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return default


def _daftar_model() -> list:
    """GEMINI_MODEL boleh berisi beberapa nama dipisah koma.

    Urutan penting: model pertama dipanggil lebih dulu, sisanya cadangan
    (diluncurkan bila model sebelumnya gagal atau belum menjawab dalam VLM_JEDA_CADANGAN detik).
    """
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


def _status_galat(e: Exception) -> str:
    """Label singkat penyebab gagal, untuk log."""
    if isinstance(e, _Sibuk):
        return "slot_penuh"
    if _kena_429(e):
        return "429"
    if (
        isinstance(e, (TimeoutError, FutTimeout))
        or "timeout" in type(e).__name__.lower()
        or "timed out" in str(e).lower()
    ):
        return "timeout"
    if isinstance(e, json.JSONDecodeError):
        return "json_rusak"
    if isinstance(e, ValueError):
        return "respons_tidak_valid"
    kode = getattr(e, "code", None) or getattr(e, "status_code", None)
    return f"error_{kode}" if kode else f"error_{type(e).__name__}"


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


def batas_waktu(
    deadline: Optional[float], per_model: float, minimum: float = 3.0
) -> Optional[float]:
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
        return types.HttpOptions(
            timeout=ms, retry_options=types.HttpRetryOptions(attempts=1)
        )
    except Exception:  # versi SDK lama tanpa retry_options
        return types.HttpOptions(timeout=ms)


def _panggil_model(
    model: str, isi, types, detik: float, tunggu_slot: float, rid: str
) -> dict:
    """Satu panggilan ke satu model. Dijalankan di thread terpisah."""
    t_antre = time.monotonic()
    if not _slot.acquire(timeout=tunggu_slot):
        raise _Sibuk(f"slot penuh ({_MAKS_BERSAMAAN} panggilan bersamaan)")
    t_mulai = time.monotonic()
    teks = None
    try:
        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            # Jawaban JSON itu pendek; batas ini mencegahnya melebar. Jangan terlalu kecil:
            # pada model yang "berpikir", token berpikir ikut terhitung dan JSON bisa terpotong.
            max_output_tokens=int(os.getenv("VLM_MAX_TOKEN_JAWABAN", "1024")),
            http_options=_opsi_http(types, detik),
        )
        resp = _get_client().models.generate_content(
            model=model, contents=isi, config=config
        )
        teks = resp.text
        d = _bersihkan(teks)
        if d.get("valid", False) and _angka(d.get("skor")) is None:
            raise ValueError("Respons tanpa skor")  # dianggap gagal, tunggu model lain
        d["_model"] = model
        pakai = getattr(resp, "usage_metadata", None)
        log.info(
            "[%s] gemini %s ok %.2fs (antre %.2fs) token masuk=%s keluar=%s valid=%s",
            rid,
            model,
            time.monotonic() - t_mulai,
            t_mulai - t_antre,
            getattr(pakai, "prompt_token_count", "?"),
            getattr(pakai, "candidates_token_count", "?"),
            d.get("valid"),
        )
        return d
    except Exception as e:
        if _kena_429(e):
            _tandai_dingin(model)
        log.warning(
            "[%s] gemini %s gagal status=%s setelah %.2fs: %s%s",
            rid,
            model,
            _status_galat(e),
            time.monotonic() - t_mulai,
            type(e).__name__,
            "",  # potongan jawaban bila JSON rusak
        )
        raise
    finally:
        _slot.release()


def nilai(
    image: Image.Image,
    deadline: Optional[float] = None,
    req_id: Optional[str] = None,
) -> dict:
    """Model dipanggil bertahap (hedged); hasil sukses pertama yang dipakai.

    - Model pertama di GEMINI_MODEL dipanggil lebih dulu. Model berikutnya (cadangan)
      baru diluncurkan bila model sebelumnya gagal, atau belum menjawab dalam
      VLM_JEDA_CADANGAN detik (default 4). Kebanyakan permintaan hanya memakai 1 panggilan.
    - Seluruh permintaan dibatasi VLM_TOTAL_DETIK (default 10), atau deadline dari
      pemanggil bila lebih awal.
    - Penolakan (valid=False) dari satu model tidak langsung diterima: model berikutnya
      segera diluncurkan untuk mengonfirmasi. Hasil valid dari model mana pun menang; bila
      dua model sepakat menolak, atau tidak ada model lain yang bisa menjawab, penolakan dipakai.
    - Panggilan Gemini bersamaan dibatasi VLM_MAX_BERSAMAAN (default 3).
    - Model yang kena 429 diistirahatkan; model lain tetap dipakai.
    - Jika semua model penuh/429, mengembalikan jawaban ringkas
      {"valid": False, "sibuk": True, "alasan": ...} (tidak melempar error).
    - Error lain (timeout, JSON rusak, dll.) yang menimpa semua model tetap dilempar.
    - req_id : ID permintaan dari lapisan HTTP (opsional); bila kosong dibuat otomatis.
      Setiap baris log permintaan ini diawali "[req_id]".
    - Hasil yang melibatkan model (valid maupun ditolak model) memuat "prompt_versi".
    """
    from google.genai import types

    rid = req_id or uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    hasil: dict = {}  # {model: status}

    def selesai(status: str, model: Optional[str] = None) -> None:
        log.info(
            "[%s] selesai hasil=%s model=%s prompt=%s total=%.2fs per_model=%s",
            rid,
            status,
            model,
            PROMPT_VERSION,
            time.monotonic() - t0,
            hasil,
        )

    semua = _daftar_model()
    if not semua:
        raise RuntimeError("Tidak ada model Gemini yang dikonfigurasi")

    models = [m for m in semua if not _sedang_dingin(m)]
    for m in semua:
        if m not in models:
            hasil[m] = "dingin"
    if not models:
        log.warning("[%s] semua model sedang diistirahatkan karena 429", rid)
        selesai("sibuk")
        return _jawaban_sibuk()

    # Gambar dikecilkan agar token lebih hemat dan respons lebih cepat.
    # 768 px = satu potongan gambar di Gemini (biasanya ~258 token).
    ukuran = int(os.getenv("VLM_UKURAN_GAMBAR", "768"))
    kualitas = int(os.getenv("VLM_KUALITAS_JPEG", "82"))
    asal = image.size
    img = image.convert(
        "RGB"
    )  # JPEG tidak mendukung transparansi (RGBA/P); hasilnya salinan
    img.thumbnail((ukuran, ukuran))  # hanya mengecilkan, tidak pernah memperbesar
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=kualitas)
    isi = [types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), PROMPT]
    per_model = float(os.getenv("VLM_TIMEOUT", "20"))
    tunggu_slot = float(os.getenv("VLM_TUNGGU_SLOT", "5"))
    total = float(os.getenv("VLM_TOTAL_DETIK", "10"))
    jeda_cadangan = float(os.getenv("VLM_JEDA_CADANGAN", "4"))

    # Batas akhir seluruh permintaan: yang lebih awal antara VLM_TOTAL_DETIK dan deadline pemanggil.
    batas_akhir = t0 + total
    if deadline is not None:
        batas_akhir = min(batas_akhir, deadline)

    detik = batas_waktu(batas_akhir, per_model)
    if detik is None:
        log.warning("[%s] batas waktu total habis sebelum memanggil model", rid)
        selesai("timeout_total")
        raise TimeoutError("batas waktu total habis")

    log.info(
        "[%s] mulai gambar=%dx%d->%dx%d jpeg=%dKB model=%s prompt=%s timeout=%.0fs",
        rid,
        asal[0],
        asal[1],
        img.size[0],
        img.size[1],
        len(buf.getvalue()) // 1024,
        models,
        PROMPT_VERSION,
        detik,
    )

    # Jangan pakai "with": blok with akan menunggu thread yang lambat selesai.
    pool = ThreadPoolExecutor(max_workers=len(models))
    futures: dict = {}  # future -> nama model (hanya yang sudah diluncurkan)
    aktif: set = set()
    antrian = list(models)  # model yang belum diluncurkan, urut prioritas
    d = None
    terakhir = None
    sibuk = False
    tolakan = None  # penolakan pertama yang belum dikonfirmasi model lain
    jadwal_cadangan = time.monotonic()  # saat model berikutnya boleh diluncurkan
    try:
        while d is None:
            sekarang = time.monotonic()

            # Luncurkan model berikutnya: bila belum ada yang berjalan (awal, atau yang
            # sebelumnya sudah gagal), atau bila jeda cadangan sudah lewat.
            if antrian and (not aktif or sekarang >= jadwal_cadangan):
                m = antrian.pop(0)
                detik_m = batas_waktu(batas_akhir, per_model)
                if detik_m is None:
                    antrian.clear()  # sisa waktu terlalu sedikit untuk model lain
                    continue
                # Timeout HTTP minimal _DEADLINE_MIN_API; loop ini tetap berhenti di batas_akhir.
                f = pool.submit(
                    _panggil_model,
                    m,
                    isi,
                    types,
                    max(detik_m, _DEADLINE_MIN_API),
                    tunggu_slot,
                    rid,
                )
                futures[f] = m
                aktif.add(f)
                jadwal_cadangan = time.monotonic() + jeda_cadangan
                continue

            if not aktif:
                break  # semua model yang bisa dicoba sudah gagal

            sisa = batas_akhir + 1.0 - sekarang  # toleransi 1 detik di atas batas akhir
            if sisa <= 0:
                terakhir = terakhir or TimeoutError("semua model melewati batas waktu")
                break
            tunggu = sisa
            if antrian:
                tunggu = min(tunggu, max(0.0, jadwal_cadangan - sekarang))

            selesai_set, _ = wait(aktif, timeout=tunggu, return_when=FIRST_COMPLETED)
            for f in selesai_set:
                aktif.discard(f)
                m = futures[f]
                try:
                    r = f.result()
                except (
                    Exception
                ) as e:  # 503 padat, 429 kuota, timeout, JSON rusak, dll.
                    hasil[m] = _status_galat(e)
                    if hasil[m] in ("slot_penuh", "429"):
                        sibuk = True
                    terakhir = e
                    continue
                if r.get("valid", False):
                    d = r
                    hasil[m] = "menang"
                    break  # yang pertama menilai valid menang
                # Penolakan belum diterima begitu saja: model lain diminta mengonfirmasi.
                hasil[m] = "menolak"
                if tolakan is None:
                    tolakan = r
                    log.info(
                        "[%s] %s menolak foto, menunggu konfirmasi model lain", rid, m
                    )
                else:
                    d = tolakan  # dua model sepakat menolak
                    hasil[d["_model"]] = "menang"
                    break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        diluncurkan = set(futures.values())
        for m in models:
            if (
                m not in diluncurkan
            ):  # tidak perlu dipanggil karena sudah ada hasil / waktu habis
                hasil.setdefault(m, "tidak_dipanggil")
            else:  # belum selesai saat pemenang sudah ada, atau saat waktu habis
                hasil.setdefault(m, "dibatalkan" if d is not None else "timeout")

    if d is None and tolakan is not None:
        # Tidak ada model lain yang sempat/bisa mengonfirmasi: pakai penolakan yang ada.
        d = tolakan
        hasil[d["_model"]] = "menang"

    if d is None:
        if sibuk:
            selesai("sibuk")
            return _jawaban_sibuk()
        selesai("gagal")
        raise terakhir or RuntimeError("Semua model gagal")

    if not d.get("valid", False):
        selesai("ditolak", d.get("_model"))
        return {
            "valid": False,
            "alasan": d.get("alasan_tolak") or "Foto belum bisa dinilai.",
            "model": d.get("_model"),
            "prompt_versi": PROMPT_VERSION,
        }

    skor = _angka(d.get("skor"))
    keyakinan = str(d.get("keyakinan", "sedang")).lower()
    if keyakinan not in ("rendah", "sedang", "tinggi"):
        keyakinan = "sedang"
    selesai(
        f"ok jenis={d.get('jenis')} skor={skor:.0f} keyakinan={keyakinan}",
        d.get("_model"),
    )
    return {
        "valid": True,
        "jenis": str(d.get("jenis") or "unknown").lower(),
        "kategori": str(d.get("kategori") or "").strip().lower(),
        "skor": skor,
        "skor_min": _angka(d.get("skor_min"), max(0.0, skor - 8)),
        "skor_maks": _angka(d.get("skor_maks"), min(100.0, skor + 8)),
        "keyakinan": keyakinan,
        "ciri": str(d.get("ciri") or ""),
        "pembusukan": str(d.get("pembusukan") or "meragukan").strip().lower(),
        "model": d.get("_model"),
        "prompt_versi": PROMPT_VERSION,
    }
