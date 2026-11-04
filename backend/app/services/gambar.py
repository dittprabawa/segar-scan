"""Membuka foto dengan aman: batas piksel dan orientasi EXIF."""

import io
import os

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = int(os.getenv("MAX_PIXELS", "25000000"))

# Penegakan batas dilakukan sendiri di bawah; matikan batas bawaan Pillow
# agar tidak ada warning/error ganda di rentang 89-178 juta piksel.
Image.MAX_IMAGE_PIXELS = None


class BukanGambar(Exception):
    pass


class TerlaluBesar(Exception):
    pass


def buka_gambar(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))  # lazy: hanya membaca header
    except (UnidentifiedImageError, OSError):
        raise BukanGambar("bukan gambar") from None

    if img.width * img.height > MAX_PIXELS:
        raise TerlaluBesar("resolusi terlalu tinggi")

    try:
        img = ImageOps.exif_transpose(img)  # tidak mengubah ukuran total piksel
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            latar = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            return Image.alpha_composite(latar, rgba).convert("RGB")
        return img.convert("RGB")
    except (OSError, ValueError, SyntaxError):  # file terpotong/rusak saat decode
        raise BukanGambar("gambar rusak") from None