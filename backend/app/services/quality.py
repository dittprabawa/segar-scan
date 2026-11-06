"""Cek kualitas foto secara lokal (cepat, tanpa AI, tanpa internet)."""

import os
from typing import Optional

import numpy as np
from PIL import Image

GELAP = float(os.getenv("QC_GELAP", "35"))      # rata-rata kecerahan 0–255
SILAU = float(os.getenv("QC_SILAU", "240"))
AREA_TERANG = float(os.getenv("QC_AREA_TERANG", "0.65"))  # proporsi piksel >= 240
BURAM = float(os.getenv("QC_BURAM", "15"))      # variansi Laplacian; kalibrasi dengan fotomu


def ukur(image: Image.Image) -> dict:
    g = np.asarray(image.convert("L").resize((256, 256)), dtype=np.float32)
    lap = (
        g[1:-1, 1:-1] * 4
        - g[:-2, 1:-1] - g[2:, 1:-1]
        - g[1:-1, :-2] - g[1:-1, 2:]
    )
    return {"kecerahan": float(g.mean()), "ketajaman": float(lap.var()),
            "area_terang": float((g >= 240).mean())}


def cek_kualitas(image: Image.Image) -> Optional[str]:
    """Kembalikan alasan penolakan (bahasa sehari-hari), atau None jika foto layak."""
    m = ukur(image)
    if m["kecerahan"] < GELAP:
        return "Foto terlalu gelap. Cari tempat yang lebih terang."
    if m["kecerahan"] > SILAU or (
        m["kecerahan"] >= 220 and m["area_terang"] >= AREA_TERANG
    ):
        return "Foto terlalu terang atau silau. Kurangi pencahayaan lalu foto ulang agar warna dan tekstur bahan terlihat."
    if m["ketajaman"] < BURAM:
        return "Foto buram. Tahan HP sebentar lalu foto ulang dari lebih dekat."
    return None
