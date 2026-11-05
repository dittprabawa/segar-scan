"""Pengujian cek kualitas foto."""

import numpy as np
from PIL import Image

from backend.app.services.quality import cek_kualitas


def _gambar(arr):
    return Image.fromarray(arr.astype("uint8"))


def test_gambar_gelap_ditolak():
    assert "gelap" in cek_kualitas(_gambar(np.full((200, 200, 3), 5)))


def test_gambar_polos_buram_ditolak():
    assert "buram" in cek_kualitas(_gambar(np.full((200, 200, 3), 120)))


def test_gambar_tajam_lolos():
    rng = np.random.default_rng(0)
    assert cek_kualitas(_gambar(rng.integers(0, 255, (200, 200, 3)))) is None


def test_area_terang_dominan_ditolak_meski_rata_rata_di_bawah_240():
    rng = np.random.default_rng(1)
    arr = rng.integers(100, 180, (256, 256, 3))
    arr[:208] = 245
    assert arr.mean() < 240
    assert "terlalu terang" in cek_kualitas(_gambar(arr))


def test_objek_jelas_dengan_latar_putih_lolos():
    rng = np.random.default_rng(2)
    arr = np.full((256, 256, 3), 255)
    arr[48:208, 48:208] = rng.integers(40, 160, (160, 160, 3))
    assert cek_kualitas(_gambar(arr)) is None


def test_gambar_putih_ditolak():
    assert "terlalu terang" in cek_kualitas(_gambar(np.full((200, 200, 3), 255)))
