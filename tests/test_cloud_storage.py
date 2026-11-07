"""Pengujian komprehensif integrasi penyimpanan online dan lokal SegarScan."""

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from PIL import Image
import pytest

from backend.app.services.cloud_storage import (
    LocalStorageBackend,
    SupabaseStorageBackend,
    VercelBlobStorageBackend,
    get_storage_backend,
    validasi_id,
)
from backend.app.services import dataset_pending
from scripts import review_dataset


def buat_foto_dummy(warna='green', ukuran=(30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', ukuran, warna).save(buffer, format='PNG')
    return buffer.getvalue()


# ============================================================================
# 1. TES VALIDASI ID & SIGV4
# ============================================================================

def test_validasi_id():
    valid = "a" * 64
    assert validasi_id(valid) == valid
    assert validasi_id(valid.upper()) == valid

    with pytest.raises(ValueError, match="ID foto tidak valid"):
        validasi_id("pendek")

    with pytest.raises(ValueError, match="ID foto tidak valid"):
        validasi_id("../" + "a" * 61)


# ============================================================================
# 2. TES SUPABASE STORAGE BACKEND
# ============================================================================

def test_supabase_save_and_retrieve():
    data = buat_foto_dummy()
    pid = hashlib.sha256(data).hexdigest()
    record = {
        "id": pid,
        "status_review": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "request_id": "req-1",
        "prediksi_sementara": {"jenis": "bayam", "status": "ok"},
    }

    mock_client = Mock(spec=httpx.Client)

    # Mock response untuk upload (POST/PUT)
    upload_res = Mock(status_code=200, text="ok")
    mock_client.post.return_value = upload_res
    mock_client.put.return_value = upload_res

    backend = SupabaseStorageBackend(
        url="https://xyz.supabase.co",
        key="test-key",
        bucket="pending",
        table="pending_scans",
        client=mock_client,
    )

    assert backend.is_online() is True
    assert backend.get_info()["tipe"] == "supabase"

    # Simpan
    result_id = backend.save_pending(pid, data, record)
    assert result_id == pid
    assert mock_client.post.call_count >= 1

    # Ambil foto
    mock_client.get.return_value = Mock(status_code=200, content=data)
    retrieved_data = backend.get_pending_image(pid)
    assert retrieved_data == data

    # Ambil metadata dari tabel DB
    db_res = Mock(status_code=200, json=lambda: [record])
    mock_client.get.return_value = db_res
    retrieved_meta = backend.get_pending_metadata(pid)
    assert retrieved_meta["id"] == pid
    assert retrieved_meta["status_review"] == "pending"


def test_supabase_list_and_verify():
    data = buat_foto_dummy()
    pid = hashlib.sha256(data).hexdigest()
    record = {
        "id": pid,
        "status_review": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "request_id": "req-1",
        "prediksi_sementara": {"jenis": "bayam", "status": "ok"},
    }

    mock_client = Mock(spec=httpx.Client)
    # Mock list via DB
    mock_client.get.return_value = Mock(status_code=200, json=lambda: [record])

    backend = SupabaseStorageBackend(
        url="https://xyz.supabase.co",
        key="test-key",
        bucket="pending",
        table="pending_scans",
        client=mock_client,
    )

    items = backend.list_pending()
    assert len(items) == 1
    assert items[0]["id"] == pid

    # Filter jenis yang cocok & tidak cocok
    assert len(backend.list_pending(jenis="bayam")) == 1
    assert len(backend.list_pending(jenis="kangkung")) == 0

    # Mark verified
    mock_client.put.return_value = Mock(status_code=200)
    mock_client.patch.return_value = Mock(status_code=200)
    verified_record = dict(record, status_review="verified", verified_at="2026-11-06T11:00:00+00:00")
    backend.mark_verified(pid, verified_record)
    assert mock_client.put.called


# ============================================================================
# 4. TES VERCEL BLOB BACKEND
# ============================================================================

def test_vercel_blob_backend():
    data = buat_foto_dummy()
    pid = hashlib.sha256(data).hexdigest()
    record = {
        "id": pid,
        "status_review": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "request_id": "req-blob",
        "prediksi_sementara": {"jenis": "wortel", "status": "ok"},
    }

    mock_client = Mock(spec=httpx.Client)
    mock_client.put.return_value = Mock(status_code=200, text="ok")

    backend = VercelBlobStorageBackend(token="blob-token-123", prefix="pending/", client=mock_client)
    assert backend.is_online() is True
    assert backend.get_info()["tipe"] == "vercel_blob"

    saved_id = backend.save_pending(pid, data, record)
    assert saved_id == pid
    assert mock_client.put.call_count == 2

    # Mock list
    mock_client.get.return_value = Mock(
        status_code=200,
        json=lambda: {
            "blobs": [
                {"pathname": f"pending/{pid}.json", "url": f"https://blob.example.com/{pid}.json"},
                {"pathname": f"pending/{pid}.img", "url": f"https://blob.example.com/{pid}.img"},
            ]
        },
        text=json.dumps(record),
    )

    items = backend.list_pending()
    assert len(items) == 1
    assert items[0]["id"] == pid


# ============================================================================
# 5. TES FACTORY & DETEKSI OTOMATIS
# ============================================================================

def test_factory_detection(monkeypatch, tmp_path):
    # Default: Local
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    backend = get_storage_backend()
    assert isinstance(backend, LocalStorageBackend)

    # Deteksi Supabase
    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "supa-secret")
    backend_supa = get_storage_backend()
    assert isinstance(backend_supa, SupabaseStorageBackend)

    # Deteksi Vercel Blob
    monkeypatch.delenv("SUPABASE_URL")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "token_123")
    backend_blob = get_storage_backend()
    assert isinstance(backend_blob, VercelBlobStorageBackend)

    # Eksplisit root selalu memakai local
    backend_root = get_storage_backend(root=tmp_path)
    assert isinstance(backend_root, LocalStorageBackend)


# ============================================================================
# 6. TES VERIFIKASI & SYNC ONLINE KE LOKAL
# ============================================================================

def test_sync_ke_lokal_dan_verifikasi_online(tmp_path, monkeypatch):
    data = buat_foto_dummy()
    pid = hashlib.sha256(data).hexdigest()
    record = {
        "id": pid,
        "status_review": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "request_id": "req-sync",
        "prediksi_sementara": {"jenis": "bayam", "status": "ok"},
    }

    mock_client = Mock(spec=httpx.Client)
    mock_client.get.return_value = Mock(status_code=200, json=lambda: [record], content=data, text=json.dumps(record))
    mock_client.put.return_value = Mock(status_code=200)
    mock_client.patch.return_value = Mock(status_code=200)

    mock_backend = SupabaseStorageBackend(
        url="https://xyz.supabase.co",
        key="key",
        bucket="pending",
        table="pending_scans",
        client=mock_client,
    )

    monkeypatch.setattr(dataset_pending, "get_storage_backend", lambda root=None, backend_type=None: mock_backend)

    # Uji sync ke direktori lokal
    local_dir = tmp_path / "pending_local"
    hasil_sync = dataset_pending.sync_ke_lokal(root=local_dir)
    assert hasil_sync["status"] == "selesai"
    assert (local_dir / f"{pid}.img").read_bytes() == data
    assert json.loads((local_dir / f"{pid}.json").read_text())["id"] == pid

    # Uji verifikasi langsung dari online
    dataset_csv = tmp_path / "verified" / "dataset.csv"
    monkeypatch.setenv("DATASET_PENDING_DIR", str(local_dir))

    dataset_pending.verifikasi(
        identitas=pid,
        jenis="bayam",
        status="ok",
        perangkat="Ponsel Android",
        split="dev",
        dataset=dataset_csv,
    )

    assert dataset_csv.exists()
    from scripts.evaluasi_jenis import baca_dataset
    baris = baca_dataset(dataset_csv, "dev")
    assert len(baris) == 1
    assert baris[0]["jenis_benar"] == "bayam"
    assert (dataset_csv.parent / "images" / f"{pid}.img").read_bytes() == data


def test_review_cli_list_and_groups_online(capsys, monkeypatch):
    data = buat_foto_dummy()
    pid = hashlib.sha256(data).hexdigest()
    record = {
        "id": pid,
        "status_review": "pending",
        "created_at": "2026-11-06T10:00:00+00:00",
        "request_id": "req-cli",
        "prediksi_sementara": {"jenis": "caisim", "status": "ok"},
        "foto": f"supabase://pending/{pid}.img",
    }

    mock_backend = Mock(spec=SupabaseStorageBackend)
    mock_backend.is_online.return_value = True
    mock_backend.list_pending.return_value = [record]
    mock_backend.get_info.return_value = {"tipe": "supabase", "bucket": "pending", "online": True}

    monkeypatch.setattr(dataset_pending, "get_storage_backend", lambda root=None, backend_type=None: mock_backend)

    # Test review_dataset list
    with patch("sys.argv", ["review_dataset.py", "--storage", "supabase", "list"]):
        review_dataset.main()
    out = capsys.readouterr().out
    assert pid in out
    assert "1 foto menunggu verifikasi" in out

    # Test review_dataset groups
    with patch("sys.argv", ["review_dataset.py", "--storage", "supabase", "groups"]):
        review_dataset.main()
    out_groups = capsys.readouterr().out
    assert "caisim: 1 foto" in out_groups
    assert "Total: 1 foto pending" in out_groups

