"""Tes ketahanan: EXIF, batas piksel, batas waktu total, dan antrean Gemini."""

import io
import threading
import time

import numpy as np
import pytest
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


def test_antrean_gemini_penuh_tidak_tersedia(monkeypatch):
    monkeypatch.setattr(vlm, "tersedia", lambda: True)
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(inference, "_gate", gate)
    monkeypatch.setattr(inference, "GATE_TUNGGU", 0)

    def nilai(im, deadline=None):
        return {"valid": True, "jenis": "bayam", "skor": 85.0, "skor_min": 80.0,
                "skor_maks": 90.0, "keyakinan": "tinggi", "ciri": "", "model": "x"}

    monkeypatch.setattr(vlm, "nilai", nilai)
    svc = InferenceService()
    foto = _foto_tajam()

    # Simulasikan seluruh slot sedang dipakai permintaan lain.
    gate.acquire()
    try:
        with pytest.raises(RuntimeError, match="Tidak ada penilai yang tersedia"):
            svc.predict(foto)
    finally:
        gate.release()

    # Setelah slot tersedia lagi, Gemini dapat melayani permintaan.
    assert svc.predict(foto)["sumber"] == "gemini"
