"""Layanan inferensi: cek kualitas -> Gemini."""

import logging
import os
import threading
import time
from typing import Optional

from PIL import Image

from backend.app.services import quality, vlm

log = logging.getLogger("segarscan")

TOTAL_TIMEOUT = float(os.getenv("TOTAL_TIMEOUT", "25"))        # detik, seluruh permintaan
MAX_GEMINI_BERSAMAAN = int(os.getenv("MAX_GEMINI_BERSAMAAN", "3"))
GATE_TUNGGU = float(os.getenv("GATE_TUNGGU", "2"))             # detik menunggu giliran
_gate = threading.BoundedSemaphore(MAX_GEMINI_BERSAMAAN)


class InferenceService:
    def __init__(self):
        # Dipakai endpoint health; inferensi hanya menggunakan Gemini.
        self.model = None

    @property
    def siap(self) -> bool:
        return vlm.tersedia()

    def predict(self, image: Image.Image, req_id: Optional[str] = None) -> dict:
        """req_id: ID permintaan dari lapisan HTTP (opsional), dipakai di semua baris log."""
        rid = req_id or "-"

        alasan = quality.cek_kualitas(image)
        if alasan:
            hasil = self._ditolak(alasan)
            self._catat(rid, hasil, penilai="kualitas")
            return hasil

        if vlm.tersedia():
            deadline = time.monotonic() + TOTAL_TIMEOUT
            if _gate.acquire(timeout=GATE_TUNGGU):
                try:
                    d = vlm.nilai(image, deadline=deadline, req_id=req_id)
                    nama_model = d.get("model")
                    versi = d.get("prompt_versi")
                    if not d["valid"]:
                        hasil = self._ditolak(d["alasan"], model=nama_model, prompt_versi=versi)
                        self._catat(rid, hasil, penilai="gemini", sibuk=bool(d.get("sibuk")))
                        return hasil
                    pembusukan = d.get("pembusukan", "meragukan")
                    if pembusukan not in {"jelas", "tidak_terlihat"}:
                        hasil = self._ditolak(
                            "Tanda pembusukan belum dapat dipastikan. "
                            "Ambil foto dekat bagian yang bercak atau rusak, "
                            "dan pisahkan objek yang kondisinya berbeda.",
                            model=nama_model, prompt_versi=versi,
                        )
                        hasil["ciri"] = d.get("ciri") or None
                        self._catat(rid, hasil, penilai="gemini")
                        return hasil
                    if pembusukan == "jelas":
                        # Batas kategori aplikasi, bukan probabilitas atau ukuran klinis.
                        d = dict(d)
                        d["skor"] = min(d["skor"], 19.0)
                        d["skor_min"] = min(d["skor_min"], d["skor"])
                        d["skor_maks"] = max(d["skor"], min(d["skor_maks"], 19.0))
                    hasil = self._ok(
                        d["skor"], d["jenis"], "gemini",
                        (d["skor_min"], d["skor_maks"]), d["keyakinan"], d["ciri"],
                        model=nama_model, prompt_versi=versi,
                        kategori=d.get("kategori"),
                    )
                    if pembusukan == "jelas":
                        hasil["label"] = "Indikasi busuk"
                        hasil["saran"] = (
                            "Terdapat indikasi pembusukan pada bagian yang terlihat. "
                            "Sebaiknya dihindari; bagian lain dalam tumpukan belum dapat dipastikan."
                        )
                    self._catat(rid, hasil, penilai="gemini")
                    return hasil
                except Exception as e:  # semua model gagal / waktu habis
                    log.warning("[%s] Gemini gagal atau timeout: %s", rid, type(e).__name__)
                finally:
                    _gate.release()
            else:
                log.warning("[%s] Antrean Gemini penuh", rid)
        raise RuntimeError("Tidak ada penilai yang tersedia")

    # --- pembantu -----------------------------------------------------
    @staticmethod
    def _catat(rid: str, hasil: dict, penilai: str, sibuk: bool = False) -> None:
        """BE12-13 dan BE17: satu baris ringkas hasil prediksi (tanpa isi gambar)."""
        log.info(
            "[%s] hasil status=%s skor=%s rentang=%s-%s jenis=%s keyakinan=%s model=%s "
            "prompt=%s penilai=%s sibuk=%s%s",
            rid,
            hasil["status"],
            hasil["skor"],
            hasil["skor_min"],
            hasil["skor_maks"],
            hasil["jenis"],
            hasil["keyakinan"],
            hasil["model"],
            hasil.get("prompt_versi"),
            penilai,
            sibuk,
            f" alasan={hasil['alasan']!r}" if hasil["status"] == "ditolak" else "",
        )

    @staticmethod
    def _ditolak(alasan: str, model: Optional[str] = None,
                 prompt_versi: Optional[str] = None) -> dict:
        return {"status": "ditolak", "skor": None, "skor_min": None, "skor_maks": None,
                "label": "Foto belum bisa dinilai", "jenis": "unknown",
                "keyakinan": None, "saran": "Coba foto ulang.", "ciri": None,
                "alasan": alasan, "sumber": None, "model": model,
                "prompt_versi": prompt_versi}

    @classmethod
    def _ok(cls, skor, jenis, sumber, rentang, keyakinan, ciri,
            model: Optional[str] = None, prompt_versi: Optional[str] = None,
            kategori: Optional[str] = None) -> dict:
        daging = kategori == "daging" or "daging" in jenis.lower() or jenis.lower() in {"ayam", "sapi", "kambing", "babi", "bebek", "domba"}
        label = ("Tampilan relatif baik" if skor >= 60 else "Perlu pemeriksaan lebih lanjut") if daging else cls._label(skor)
        saran = ("Penilaian visual saja; kesegaran dan keamanan daging tidak dapat dipastikan dari foto.") if daging else cls._saran(skor)
        return {"status": "ok", "skor": round(skor, 1),
                "skor_min": round(rentang[0], 1), "skor_maks": round(rentang[1], 1),
                "label": label, "jenis": jenis, "keyakinan": keyakinan,
                "saran": saran, "ciri": ciri or None,
                "alasan": None, "sumber": sumber, "model": model,
                "prompt_versi": prompt_versi}

    @staticmethod
    def _label(skor: float) -> str:
        if skor >= 80: return "Sangat segar"
        if skor >= 60: return "Segar"
        if skor >= 40: return "Segera olah"
        if skor >= 20: return "Layu"
        return "Tidak layak"

    @staticmethod
    def _saran(skor: float) -> str:
        if skor >= 60: return "Layak dibeli"
        if skor >= 40: return "Beli hanya untuk dimasak hari ini"
        return "Sebaiknya dihindari"