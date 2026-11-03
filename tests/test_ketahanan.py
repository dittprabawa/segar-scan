"""Tes ketahanan: EXIF, batas piksel, batas waktu total, dan antrean Gemini."""

import io
import threading
import time

import numpy as np
from PIL import Image

from backend.app.services import gambar, inference, vlm
from backend.app.services.inference import InferenceService


def _jpeg_terputar():
    """Gambar 40x20 dengan tag EXIF orientasi 6 (harus diputar menjadi 20x40)."""
    img = Image.new("RGB", (40, 20), (10, 200, 10))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_exif_diterapkan():
    hasil = gambar.buka_gambar(_jpeg_terputar())
    assert hasil.size == (20, 40)


def test_gambar_kebanyakan_piksel_ditolak(monkeypatch):
    monkeypatch.setattr(gambar, "MAX_PIXELS", 1000)
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "PNG")
    try:
        gambar.buka_gambar(buf.getvalue())
        assert False, "seharusnya ditolak"
    except gambar.TerlaluBesar:
        pass


def test_bukan_gambar():
    try:
        gambar.buka_gambar(b"ini bukan gambar")
        assert False
    except gambar.BukanGambar:
        pass


def test_batas_waktu_total():
    assert vlm.batas_waktu(None, 20) == 20
    sekarang = time.monotonic()
    assert vlm.batas_waktu(sekarang + 100, 20) == 20          # sisa banyak: pakai batas per model
    assert 9 < vlm.batas_waktu(sekarang + 10, 20) <= 10        # sisa kecil: dipotong
    assert vlm.batas_waktu(sekarang + 1, 20) is None           # sisa < 3 dtk: berhenti


def _foto_tajam():
    rng = np.random.default_rng(0)
    return Image.fromarray(rng.integers(0, 255, (200, 200, 3)).astype("uint8"))


def test_antrean_gemini_penuh_pakai_cadangan(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(inference, "_gate", threading.BoundedSemaphore(1))
    monkeypatch.setattr(inference, "GATE_TUNGGU", 0.1)

    def lambat(im, deadline=None):
        time.sleep(0.6)
        return {"valid": True, "jenis": "bayam", "skor": 85.0, "skor_min": 80.0,
                "skor_maks": 90.0, "keyakinan": "tinggi", "ciri": "", "model": "x"}

    monkeypatch.setattr(vlm, "nilai", lambat)
    svc = InferenceService("tidak_ada.pt")
    svc.model, svc._predict_lokal = object(), (lambda im, m: 30.0)

    hasil = {}
    t = threading.Thread(target=lambda: hasil.setdefault("a", svc.predict(_foto_tajam())))
    t.start()
    time.sleep(0.15)                       # permintaan pertama sedang memegang giliran
    kedua = svc.predict(_foto_tajam())     # permintaan kedua tidak kebagian
    t.join()

    assert hasil["a"]["sumber"] == "gemini"
    assert kedua["sumber"] == "model_lokal"
