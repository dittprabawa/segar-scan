"""SegarScan FastAPI Backend."""

import os
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import logging
import time
import uuid

log = logging.getLogger("segarscan")

from dotenv import load_dotenv

load_dotenv()  # baca .env sebelum layanan dibuat

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from backend.app.slowapi_compat import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.types import ASGIApp, Scope, Receive, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend.app.schemas import PredictionResponse
from backend.app.services import dataset_pending, jenis_lokal, vlm
from backend.app.services.gambar import (
    BukanGambar,
    TerlaluBesar,
    buka_gambar,
)
from backend.app.services.inference import InferenceService
from backend.app.services.statistik import Statistik

MAKS_BYTES = 5 * 1024 * 1024  # 5 MB
RATE_LIMIT = os.getenv("RATE_LIMIT", "10/minute")  # per IP

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
# Nama logger disamakan dengan vlm.py dan inference.py
logger = logging.getLogger("segarscan")


async def cleanup_pending():
    while True:
        if dataset_pending.aktif():
            try:
                result = await asyncio.to_thread(dataset_pending.bersihkan)
                if result["id_kedaluwarsa"]:
                    logger.info(
                        "Dataset cleanup: %d foto pending kedaluwarsa",
                        len(result["id_kedaluwarsa"]),
                    )
            except Exception as exc:
                logger.error("Dataset cleanup gagal: %s", type(exc).__name__)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(cleanup_pending())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="SegarScan API", version="1.2.0", lifespan=lifespan)
app.state.statistik = Statistik()


def _rid(request: Request) -> str:
    """ID permintaan yang dibuat di log_requests ('-' bila belum ada)."""
    return getattr(request.state, "req_id", "-")


class LimitUploadSizeMiddleware:
    """Batasi body sebelum parser multipart, termasuk unggahan tanpa Content-Length."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/predict"
        ):
            return await self.app(scope, receive, send)
        limit = MAKS_BYTES + 64 * 1024
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse(
                status_code=400, content={"detail": "Content-Length tidak valid"}
            )(scope, receive, send)
        if length > limit:
            return await JSONResponse(
                status_code=413, content={"detail": "Foto terlalu besar (maks 5 MB)"}
            )(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                return await JSONResponse(
                    status_code=413,
                    content={"detail": "Foto terlalu besar (maks 5 MB)"},
                )(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        sent = False

        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


app.add_middleware(LimitUploadSizeMiddleware)


async def log_requests(request: Request, call_next):
    req_id = uuid.uuid4().hex[:8]
    request.state.req_id = (
        req_id  # dipakai endpoint, handler error, dan diteruskan ke service
    )
    start_time = time.perf_counter()

    # IP yang sudah disesuaikan oleh ProxyHeadersMiddleware (lapisan terluar)
    client_ip = request.client.host if request.client else "Unknown"

    try:
        response = await call_next(request)
    except Exception as exc:
        # Jangan tulis pesan exception yang dapat mengandung data sensitif.
        logger.error("[%s] Error tak tertangani: %s", req_id, type(exc).__name__)
        response = JSONResponse(
            status_code=500,
            content={"detail": "Terjadi gangguan pada server. Coba lagi sebentar."},
        )
    process_time = time.perf_counter() - start_time

    if request.method == "POST" and request.url.path == "/predict":
        request.app.state.statistik.catat(
            process_time * 1000,
            ditolak=response.status_code == 200
            and getattr(request.state, "prediksi_ditolak", False),
            error=response.status_code >= 400,
        )

    response.headers["X-Request-ID"] = req_id
    logger.info(
        f"[{req_id}] REQ | IP: {client_ip} | {request.method} {request.url.path} | "
        f"Status: {response.status_code} | Durasi: {process_time:.3f}s"
    )
    return response


app.middleware("http")(log_requests)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

TRUSTED_PROXIES = [
    h.strip() for h in os.getenv("TRUSTED_PROXIES", "127.0.0.1").split(",") if h.strip()
]
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts="*" if "*" in TRUSTED_PROXIES else TRUSTED_PROXIES,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
service = InferenceService()


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning(
        f"[{_rid(request)}] Rate Limit tercapai untuk IP: {get_remote_address(request)}"
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Terlalu banyak permintaan. Coba lagi sebentar lagi."},
        headers={"Retry-After": "60"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.warning("[%s] Validasi gagal: %d field", _rid(request), len(exc.errors()))
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
    rid = _rid(request)
    siap = getattr(service, "siap", service.model is not None)
    if not siap:
        raise HTTPException(
            status_code=503,
            detail="Model belum tersedia",
            headers={"Retry-After": "30"},
        )

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

    hasil = {"status": "error", "alasan": "Penilai gagal"}
    try:
        hasil = service.predict(image, req_id=rid)
        request.state.prediksi_ditolak = hasil.get("status") == "ditolak"
        if hasil.get("status") == "ok":
            try:
                kandidat = jenis_lokal.predict(image)
                if kandidat is not None:
                    hasil["jenis_lokal"] = kandidat
            except Exception as exc:
                logger.error(
                    "[%s] Model jenis lokal gagal: %s", rid, type(exc).__name__
                )
        return hasil
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Penilai sedang tidak tersedia. Coba lagi sebentar.",
            headers={"Retry-After": "30"},
        )
    finally:
        image.close()
        if dataset_pending.aktif():
            try:
                identitas = dataset_pending.simpan(data, hasil, rid)
                logger.info("[%s] Dataset pending: %s", rid, identitas)
            except Exception as exc:
                # Gangguan disk tidak mengubah hasil penilaian pengguna.
                logger.error(
                    "[%s] Gagal menyimpan dataset pending: %s", rid, type(exc).__name__
                )


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
