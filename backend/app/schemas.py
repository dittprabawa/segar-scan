"""Pydantic schemas untuk SegerScan API."""

from typing import Optional

from pydantic import BaseModel, Field


class PredictionResponse(BaseModel):
    status: str = Field("ok", description="ok atau ditolak")
    skor: Optional[float] = Field(None, ge=0, le=100, description="Perkiraan skor kesegaran (0–100)")
    skor_min: Optional[float] = None
    skor_maks: Optional[float] = None
    label: str = Field(..., description="Kategori kesegaran")
    jenis: str = Field(..., description="Jenis sayuran yang terdeteksi")
    keyakinan: Optional[str] = Field(None, description="rendah / sedang / tinggi")
    saran: Optional[str] = None
    ciri: Optional[str] = None
    alasan: Optional[str] = Field(None, description="Alasan jika foto ditolak")
    sumber: Optional[str] = Field(None, description="gemini atau model_lokal")
