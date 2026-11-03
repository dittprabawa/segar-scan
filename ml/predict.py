"""Fungsi inferensi 1 gambar (skala 0–100, prapemrosesan sama dengan training)."""

import torch
from PIL import Image

from ml.dataset import build_transform
from ml.model import build_model


def load_model(weights_path: str) -> torch.nn.Module:
    model = build_model(pretrained=False)
    model.load_state_dict(torch.load(weights_path, map_location="cpu"))
    model.eval()
    return model


def predict(image: Image.Image, model: torch.nn.Module) -> float:
    """Kembalikan skor kesegaran 0–100 untuk satu gambar."""
    x = build_transform(augment=False)(image).unsqueeze(0)
    with torch.no_grad():
        y = model(x).item()  # keluaran model berskala 0–1 (sesuai training)
    return round(max(0.0, min(100.0, y * 100)), 1)
