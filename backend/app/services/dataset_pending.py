"""Antrean foto dan metadata untuk verifikasi manusia: mendukung penyimpanan lokal dan online."""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

try:
    import fcntl
except ImportError:
    fcntl = None

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Optional, Dict, Any, List

from backend.app.services.storage_policy import check_capacity, positive, usage
from backend.app.services.cloud_storage import get_storage_backend, validasi_id, MAX_PHOTO_BYTES

logger = logging.getLogger("segarscan.pending")

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "data" / "pending"


def direktori() -> Path:
    return Path(os.getenv("DATASET_PENDING_DIR", str(DEFAULT_DIR)))


def aktif() -> bool:
    return os.getenv("DATASET_CAPTURE", "1").lower() in {"1", "true", "yes"}


@contextmanager
def kunci(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def tulis(path: Path, data: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def metadata(root: Path, identitas: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", identitas):
        raise ValueError("ID foto tidak valid")
    path = root / (identitas + ".json")
    if path.is_symlink() or (root / (identitas + ".img")).is_symlink():
        raise ValueError("Symlink tidak boleh digunakan untuk foto/metadata pending")
    return path


def _bersihkan(root: Path, dry_run: bool = False, now: Optional[datetime] = None) -> List[str]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=positive("DATASET_RETENTION_DAYS", 30))
    removed = []
    if not root.exists():
        return removed
    for path in root.glob("*.json"):
        if not re.fullmatch(r"[0-9a-f]{64}", path.stem) or path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(record["created_at"])
            if record.get("status_review") != "pending" or created >= cutoff:
                continue
        except (ValueError, KeyError, TypeError):
            continue  # Jangan menghapus metadata yang tidak dipahami.
        image = path.with_suffix(".img")
        if image.is_symlink():
            continue
        removed.append(path.stem)
        if not dry_run:
            image.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
    return removed


def bersihkan(root: Optional[Path] = None, dry_run: bool = False, storage: Optional[str] = None) -> Dict[str, Any]:
    backend = get_storage_backend(root=root, backend_type=storage)
    if not backend.is_online():
        target_root = Path(root) if root is not None else direktori()
        with kunci(target_root):
            removed = _bersihkan(target_root, dry_run=dry_run)
            return {
                "id_kedaluwarsa": removed,
                "dry_run": dry_run,
                "bytes_tersimpan": usage(target_root) if target_root.exists() else 0,
            }
    return backend.cleanup_expired(retention_days=positive("DATASET_RETENTION_DAYS", 30), dry_run=dry_run)


def simpan(data: bytes, hasil: Dict[str, Any], req_id: str, root: Optional[Path] = None, storage: Optional[str] = None) -> str:
    identitas = hashlib.sha256(data).hexdigest()
    backend = get_storage_backend(root=root, backend_type=storage)

    if not backend.is_online():
        # Alur lokal murni (konsisten dengan implementasi awal)
        target_root = Path(root) if root is not None else direktori()
        with kunci(target_root):
            _bersihkan(target_root)
            path = metadata(target_root, identitas)
            if path.exists():
                return identitas
            record = {
                "id": identitas,
                "status_review": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "request_id": req_id,
                "prediksi_sementara": hasil,
            }
            payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            check_capacity(target_root, len(data) + len(payload), "DATASET_PENDING_MAX_MB", 512)
            image = target_root / (identitas + ".img")
            tulis(image, data)
            try:
                tulis(path, payload)
            except Exception:
                image.unlink(missing_ok=True)
                raise
        return identitas

    # Alur Cloud (Supabase, S3/R2, Vercel Blob)
    record = {
        "id": identitas,
        "status_review": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "prediksi_sementara": hasil,
    }
    backend.save_pending(identitas, data, record)

    # Coba cache lokal jika direktori lokal tersedia dan writable (abaikan jika di Vercel read-only)
    try:
        local_dir = direktori()
        if local_dir.exists() and os.access(local_dir, os.W_OK):
            img_path = local_dir / f"{identitas}.img"
            json_path = local_dir / f"{identitas}.json"
            if not img_path.exists():
                tulis(img_path, data)
            if not json_path.exists():
                tulis(json_path, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError:
        pass  # Di lingkungan serverless read-only seperti Vercel, kegagalan disk lokal diabaikan

    return identitas


def ambil_foto(identitas: str, root: Optional[Path] = None, storage: Optional[str] = None) -> bytes:
    validasi_id(identitas)
    backend = get_storage_backend(root=root, backend_type=storage)
    return backend.get_pending_image(identitas)


def ambil_metadata(identitas: str, root: Optional[Path] = None, storage: Optional[str] = None) -> Dict[str, Any]:
    validasi_id(identitas)
    backend = get_storage_backend(root=root, backend_type=storage)
    return backend.get_pending_metadata(identitas)


def daftar_pending(root: Optional[Path] = None, jenis: Optional[str] = None, storage: Optional[str] = None) -> List[Dict[str, Any]]:
    backend = get_storage_backend(root=root, backend_type=storage)
    return backend.list_pending(jenis=jenis)


def verifikasi(
    identitas: str,
    jenis: str,
    status: str,
    perangkat: str,
    split: str,
    dataset: Any,
    root: Optional[Path] = None,
    storage: Optional[str] = None,
) -> None:
    from scripts.update_dataset import update_dataset

    validasi_id(identitas)
    backend = get_storage_backend(root=root, backend_type=storage)

    if not backend.is_online():
        target_root = Path(root) if root is not None else direktori()
        with kunci(target_root):
            path = metadata(target_root, identitas)
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["status_review"] != "pending":
                raise ValueError("Foto sudah diverifikasi")
            image = target_root / (identitas + ".img")
            if hashlib.sha256(image.read_bytes()).hexdigest() != identitas:
                raise ValueError("Isi foto pending berubah")
            record.update(
                status_review="verified",
                verified_at=datetime.now(timezone.utc).isoformat(),
                label_manusia={"jenis": jenis, "status": status, "perangkat": perangkat, "split": split},
                dataset=str(Path(dataset).resolve()),
            )
            payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            check_capacity(target_root, len(payload), "DATASET_PENDING_MAX_MB", 512)
            update_dataset(image, jenis, status, perangkat, split, dataset)
            tulis(path, payload)
        return

    # Verifikasi dari backend online
    record = backend.get_pending_metadata(identitas)
    if record.get("status_review") != "pending":
        raise ValueError("Foto sudah diverifikasi")

    # Ambil byte gambar (dari online jika belum ada di lokal)
    image_bytes = backend.get_pending_image(identitas)
    if hashlib.sha256(image_bytes).hexdigest() != identitas:
        raise ValueError("Isi foto pending berubah")

    # Simpan sementara atau di cache lokal untuk update_dataset
    local_dir = direktori()
    local_dir.mkdir(parents=True, exist_ok=True)
    local_img = local_dir / f"{identitas}.img"
    if not local_img.exists():
        tulis(local_img, image_bytes)

    record.update(
        status_review="verified",
        verified_at=datetime.now(timezone.utc).isoformat(),
        label_manusia={"jenis": jenis, "status": status, "perangkat": perangkat, "split": split},
        dataset=str(Path(dataset).resolve()),
    )

    # Masukkan ke CSV & images/ terverifikasi lokal
    update_dataset(local_img, jenis, status, perangkat, split, dataset)

    # Tandai terverifikasi di backend online
    backend.mark_verified(identitas, record)

    # Perbarui juga metadata lokal jika file ada
    local_json = local_dir / f"{identitas}.json"
    try:
        tulis(local_json, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError:
        pass


def sync_ke_lokal(root: Optional[Path] = None, storage: Optional[str] = None) -> Dict[str, Any]:
    """Unduh semua foto dan metadata pending dari online storage ke disk lokal."""
    backend = get_storage_backend(root=None, backend_type=storage)
    if not backend.is_online():
        return {"status": "dilewati", "alasan": "Storage backend saat ini adalah lokal"}

    target_root = Path(root) if root is not None else direktori()
    target_root.mkdir(parents=True, exist_ok=True)

    items = backend.list_pending()
    unduh_foto = 0
    unduh_meta = 0
    sudah_ada = 0

    for item in items:
        pid = item["id"]
        img_path = target_root / f"{pid}.img"
        json_path = target_root / f"{pid}.json"

        # Unduh foto jika belum ada atau rusak
        if not img_path.exists() or hashlib.sha256(img_path.read_bytes()).hexdigest() != pid:
            foto_bytes = backend.get_pending_image(pid)
            tulis(img_path, foto_bytes)
            unduh_foto += 1
        else:
            sudah_ada += 1

        # Tulis/perbarui metadata lokal
        tulis(json_path, json.dumps(item, ensure_ascii=False, indent=2).encode("utf-8"))
        unduh_meta += 1

    return {
        "status": "selesai",
        "total_pending_online": len(items),
        "foto_baru_diunduh": unduh_foto,
        "metadata_diperbarui": unduh_meta,
        "foto_sudah_ada": sudah_ada,
        "direktori_lokal": str(target_root),
    }


def info_penyimpanan(root: Optional[Path] = None, storage: Optional[str] = None) -> Dict[str, Any]:
    """Cek informasi penyimpanan yang sedang aktif."""
    backend = get_storage_backend(root=root, backend_type=storage)
    return backend.get_info()
