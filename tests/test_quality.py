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
