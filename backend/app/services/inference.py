"""Layanan inferensi: cek kualitas -> Gemini -> cadangan model lokal."""

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
    def __init__(self, weights_path: Optional[str] = None):
        weights_path = weights_path or os.getenv("MODEL_PATH", "models/segarscan.pt")
        self.model = None
        self._predict_lokal = None
        # Model lokal bersifat opsional: server tetap jalan walau torch/ml/model tidak ada
        if os.path.exists(weights_path):
            try:
                from ml.predict import load_model, predict

                self.model = load_model(weights_path)
                self._predict_lokal = predict
            except Exception as e:
                log.warning("Model lokal tidak dimuat: %s", e)

    @property
    def siap(self) -> bool:
        return self.model is not None or vlm.tersedia()

    def predict(self, image: Image.Image) -> dict:
        alasan = quality.cek_kualitas(image)
        if alasan:
            return self._ditolak(alasan)

        if vlm.tersedia():
            deadline = time.monotonic() + TOTAL_TIMEOUT
            if _gate.acquire(timeout=GATE_TUNGGU):
                try:
                    d = vlm.nilai(image, deadline=deadline)
                    if not d["valid"]:
                        return self._ditolak(d["alasan"])
                    log.info("Dinilai Gemini model=%s", d.get("model"))
                    return self._ok(
                        d["skor"], d["jenis"], "gemini",
                        (d["skor_min"], d["skor_maks"]), d["keyakinan"], d["ciri"],
                    )
                except Exception as e:  # semua model gagal / waktu habis
                    log.warning("Gemini gagal, pindah ke model lokal: %s", str(e)[:200])
                finally:
                    _gate.release()
            else:
                log.warning("Antrean Gemini penuh, memakai cadangan")

        if self.model is not None and self._predict_lokal is not None:
            skor = self._predict_lokal(image, self.model)
            return self._ok(skor, "unknown", "model_lokal",
                            (max(0.0, skor - 15), min(100.0, skor + 15)), "rendah", "")

        raise RuntimeError("Tidak ada penilai yang tersedia")

    # --- pembantu -----------------------------------------------------
    @staticmethod
    def _ditolak(alasan: str) -> dict:
        return {"status": "ditolak", "skor": None, "skor_min": None, "skor_maks": None,
                "label": "Foto belum bisa dinilai", "jenis": "unknown",
                "keyakinan": None, "saran": "Coba foto ulang.", "ciri": None,
                "alasan": alasan, "sumber": None}

    @classmethod
    def _ok(cls, skor, jenis, sumber, rentang, keyakinan, ciri) -> dict:
        return {"status": "ok", "skor": round(skor, 1),
                "skor_min": round(rentang[0], 1), "skor_maks": round(rentang[1], 1),
                "label": cls._label(skor), "jenis": jenis, "keyakinan": keyakinan,
                "saran": cls._saran(skor), "ciri": ciri or None,
                "alasan": None, "sumber": sumber}

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
