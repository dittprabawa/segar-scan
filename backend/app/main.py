"""SegerScan FastAPI Backend."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # baca .env sebelum layanan dibuat

from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from backend.app.schemas import PredictionResponse  # noqa: E402
from backend.app.services import vlm  # noqa: E402
from backend.app.services.gambar import BukanGambar, TerlaluBesar, buka_gambar  # noqa: E402
from backend.app.services.inference import InferenceService  # noqa: E402

MAKS_BYTES = 5 * 1024 * 1024  # 5 MB

app = FastAPI(title="SegerScan API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = InferenceService()


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    """Kembalikan pesan yang lebih jelas saat field 'file' tidak dikirim."""
    return JSONResponse(
        status_code=422,
        content={"detail": "Field 'file' wajib dikirim sebagai multipart/form-data."},
    )


@app.get("/")
def root():
    return {"message": "SegerScan API is running"}


@app.get("/health")
def health():
    """Cek status server, ketersediaan Gemini, dan model lokal."""
    return {
        "status": "ok",
        "gemini": vlm.tersedia(),
        "model_lokal": service.model is not None,
        "siap": service.siap,
    }


@app.post("/predict", response_model=PredictionResponse)
def predict(request: Request, file: UploadFile = File(...)):
    # Sengaja `def` (bukan `async def`): FastAPI menjalankannya di thread pool,
    # sehingga menunggu Gemini tidak memblokir permintaan pengguna lain.
    siap = getattr(service, "siap", service.model is not None)
    if not siap:
        raise HTTPException(status_code=503, detail="Model belum tersedia")

    # Tolak lebih awal bila ukuran yang diumumkan sudah kebesaran
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAKS_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="Foto terlalu besar (maks 5 MB)")

    # Baca paling banyak MAKS_BYTES + 1 byte, bukan seluruh berkas
    data = file.file.read(MAKS_BYTES + 1)
    if len(data) > MAKS_BYTES:
        raise HTTPException(status_code=413, detail="Foto terlalu besar (maks 5 MB)")

    try:
        image = buka_gambar(data)
    except TerlaluBesar:
        raise HTTPException(status_code=413, detail="Foto terlalu besar. Coba foto ulang.")
    except BukanGambar:
        raise HTTPException(status_code=400, detail="File bukan gambar yang valid")

    try:
        return service.predict(image)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Penilai sedang tidak tersedia")


# Taruh paling bawah: menyajikan frontend dari server yang sama
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
