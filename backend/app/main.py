"""SegerScan FastAPI Backend."""

import os
from pathlib import Path
import logging

log = logging.getLogger("segarscan")

from dotenv import load_dotenv

load_dotenv()  # baca .env sebelum layanan dibuat

from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from slowapi import Limiter  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402

from backend.app.schemas import PredictionResponse  # noqa: E402
from backend.app.services import vlm  # noqa: E402
from backend.app.services.gambar import (
    BukanGambar,
    TerlaluBesar,
    buka_gambar,
)  # noqa: E402
from backend.app.services.inference import InferenceService  # noqa: E402

MAKS_BYTES = 5 * 1024 * 1024  # 5 MB
RATE_LIMIT = os.getenv("RATE_LIMIT", "10/minute")  # per IP

# Konfigurasi Logging (BE12-BE13)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
# Nama logger disamakan dengan vlm.py dan inference.py
logger = logging.getLogger("segarscan")

app = FastAPI(title="SegerScan API", version="1.2.0")


def _rid(request: Request) -> str:
    """ID permintaan yang dibuat di log_requests ('-' bila belum ada)."""
    return getattr(request.state, "req_id", "-")


# BE3: Middleware untuk menolak request berdasarkan Content-Length sebelum diproses
class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/predict":
            cl = request.headers.get("content-length")
            try:
                terlalu_besar = bool(cl) and int(cl) > (MAKS_BYTES + 64 * 1024)
            except ValueError:
                # Content-Length bukan angka: biarkan lolos, validasi stream di endpoint yang menolak
                terlalu_besar = False
            if terlalu_besar:
                ip = request.client.host if request.client else "Unknown"
                logger.warning(
                    f"[{_rid(request)}] File ditolak (BE3): Ukuran {cl} bytes melebihi batas. IP: {ip}"
                )
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Foto terlalu besar (maks 5 MB)"},
                )
        return await call_next(request)


app.add_middleware(LimitUploadSizeMiddleware)


# BE12-BE13: Middleware untuk Logging ID Permintaan, IP, Durasi, dan Status.
# Didaftarkan SETELAH LimitUploadSizeMiddleware supaya menjadi lapisan di luarnya:
# dengan begitu penolakan 413 dari BE3 juga tercatat dengan ID dan IP.
async def log_requests(request: Request, call_next):
    req_id = uuid.uuid4().hex[:8]
    request.state.req_id = (
        req_id  # dipakai endpoint, handler error, dan diteruskan ke service
    )
    start_time = time.time()

    # IP yang sudah disesuaikan oleh ProxyHeadersMiddleware (lapisan terluar)
    client_ip = request.client.host if request.client else "Unknown"

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            f"[{req_id}] REQ | IP: {client_ip} | {request.method} {request.url.path} | "
            f"Error tak tertangani | Durasi: {time.time() - start_time:.3f}s"
        )
        raise
    process_time = time.time() - start_time

    response.headers["X-Request-ID"] = req_id
    logger.info(
        f"[{req_id}] REQ | IP: {client_ip} | {request.method} {request.url.path} | "
        f"Status: {response.status_code} | Durasi: {process_time:.3f}s"
    )
    return response


app.middleware("http")(log_requests)

# BE6: Batasi CORS hanya untuk origin yang diizinkan melalui environment variable
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # Tidak lagi ["*"]
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# BE8: percayai X-Forwarded-For hanya dari proxy yang dikenal.
# Ditambahkan PALING AKHIR agar jadi lapisan terluar, sehingga log_requests juga melihat IP asli.
# Isi env TRUSTED_PROXIES dengan IP proxy dipisah koma. Default "127.0.0.1" (Nginx di mesin yang sama).
# Di Vercel isi "*": Vercel sendiri menimpa X-Forwarded-For sehingga tidak bisa dipalsukan dari luar.
TRUSTED_PROXIES = [
    h.strip() for h in os.getenv("TRUSTED_PROXIES", "127.0.0.1").split(",") if h.strip()
]
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts="*" if "*" in TRUSTED_PROXIES else TRUSTED_PROXIES,
)

# BE5 & BE7: Rate limit per IP
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
service = InferenceService()


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning(
        f"[{_rid(request)}] Rate Limit tercapai (BE5/BE7) untuk IP: {get_remote_address(request)}"
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Terlalu banyak permintaan. Coba lagi sebentar lagi."},
        headers={"Retry-After": "60"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"[{_rid(request)}] Validasi gagal: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": "Field 'file' wajib dikirim sebagai multipart/form-data."},
    )


@app.get("/")
def root():
    return RedirectResponse(url="/ui/", status_code=307)


@app.get("/health")
def health():
    """Cek status server, ketersediaan Gemini, dan model lokal."""
    return {
        "status": "ok",
        "versi": app.version,
        "versi_prompt": vlm.PROMPT_VERSION,
        "statistik": app.state.statistik.ringkasan(),
        "gemini": vlm.tersedia(),
        "model_lokal": service.model is not None,
        "siap": service.siap,
    }


@app.post("/predict", response_model=PredictionResponse)
@limiter.limit(RATE_LIMIT)
def predict(request: Request, file: UploadFile = File(...)):
    # Sengaja `def` (bukan `async def`): FastAPI menjalankannya di thread pool,
    # sehingga menunggu Gemini tidak memblokir permintaan pengguna lain.
    siap = getattr(service, "siap", service.model is not None)
    if not siap:
        raise HTTPException(status_code=503, detail="Model belum tersedia")

    # Validasi lapis kedua jika Content-Length dimanipulasi atau tidak dikirim (chunked)
    data = file.file.read(MAKS_BYTES + 1)
    if len(data) > MAKS_BYTES:
        logger.warning(f"[{rid}] Penolakan: Ukuran data stream melebihi batas 5MB.")
        raise HTTPException(status_code=413, detail="Foto terlalu besar (maks 5 MB)")

    try:
        image = buka_gambar(data)
    except TerlaluBesar:
        raise HTTPException(
            status_code=413, detail="Foto terlalu besar. Coba foto ulang."
        )
    except BukanGambar:
        logger.warning(
            f"[{rid}] Penolakan: File yang diunggah bukan gambar ({file.content_type})."
        )
        raise HTTPException(status_code=400, detail="File bukan gambar yang valid")

    try:
        return service.predict(image)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Penilai sedang tidak tersedia")


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
