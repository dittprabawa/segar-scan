"""Membuka foto dengan aman: batas piksel dan orientasi EXIF."""

import io
import os

from PIL import Image, ImageOps

MAX_PIXELS = int(os.getenv("MAX_PIXELS", "25000000"))


class BukanGambar(Exception):
    pass


class TerlaluBesar(Exception):
    pass


def buka_gambar(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))  # lazy: baru membaca header
    except Image.DecompressionBombError:
        raise TerlaluBesar("resolusi terlalu tinggi")
    except Exception:
        raise BukanGambar("bukan gambar")
    if img.width * img.height > MAX_PIXELS:
        raise TerlaluBesar("resolusi terlalu tinggi")
    try:
        return ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise BukanGambar("gambar rusak")
