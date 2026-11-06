"""Abstraksi penyimpanan dataset pending: lokal, Supabase, S3/Cloudflare R2, dan Vercel Blob."""

import abc
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
from typing import Optional, List, Dict, Any

import httpx

from backend.app.services.storage_policy import check_capacity, positive, usage

logger = logging.getLogger("segarscan.storage")

ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB


def validasi_id(identitas: str) -> str:
    """Pastikan identitas adalah 64 karakter hex sha256 valid."""
    identitas = str(identitas or "").strip().lower()
    if not ID_PATTERN.fullmatch(identitas):
        raise ValueError("ID foto tidak valid (harus 64 karakter heksadesimal SHA-256)")
    return identitas


def normalisasi(value: Any) -> str:
    """Normalisasi string untuk pencocokan jenis."""
    return " ".join(str(value or "").casefold().split())


class StorageBackend(abc.ABC):
    """Antarmuka penyimpanan untuk foto dan metadata pending."""

    @abc.abstractmethod
    def save_pending(self, photo_id: str, data: bytes, record: Dict[str, Any]) -> str:
        """Simpan byte foto dan metadata JSON."""
        pass

    @abc.abstractmethod
    def get_pending_image(self, photo_id: str) -> bytes:
        """Ambil byte foto pending."""
        pass

    @abc.abstractmethod
    def get_pending_metadata(self, photo_id: str) -> Dict[str, Any]:
        """Ambil metadata dict pending."""
        pass

    @abc.abstractmethod
    def list_pending(self, jenis: Optional[str] = None) -> List[Dict[str, Any]]:
        """Daftar metadata berstatus 'pending'."""
        pass

    @abc.abstractmethod
    def mark_verified(self, photo_id: str, verified_record: Dict[str, Any]) -> None:
        """Perbarui status review menjadi verified beserta label manusia."""
        pass

    @abc.abstractmethod
    def cleanup_expired(self, retention_days: int = 30, dry_run: bool = False) -> Dict[str, Any]:
        """Bersihkan data pending yang melebihi batas retensi."""
        pass

    @abc.abstractmethod
    def is_online(self) -> bool:
        """True jika backend ini menyimpan ke cloud."""
        pass

    @abc.abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """Informasi status konfigurasi storage backend."""
        pass


# ============================================================================
# 1. LOCAL STORAGE BACKEND
# ============================================================================

class LocalStorageBackend(StorageBackend):
    """Penyimpanan pending di filesystem lokal."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def is_online(self) -> bool:
        return False

    def get_info(self) -> Dict[str, Any]:
        return {
            "tipe": "local",
            "path": str(self.root),
            "online": False,
            "bytes_tersimpan": usage(self.root) if self.root.exists() else 0,
        }

    def _lock(self):
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return (self.root / ".lock").open("a")

    def _tulis_atomik(self, path: Path, data: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _metadata_path(self, photo_id: str) -> Path:
        validasi_id(photo_id)
        path = self.root / f"{photo_id}.json"
        img = self.root / f"{photo_id}.img"
        if path.is_symlink() or img.is_symlink():
            raise ValueError("Symlink tidak boleh digunakan untuk foto/metadata pending")
        return path

    def save_pending(self, photo_id: str, data: bytes, record: Dict[str, Any]) -> str:
        validasi_id(photo_id)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError(f"Foto melebihi batas maksimal {MAX_PHOTO_BYTES} byte")
        import fcntl
        with self._lock() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._bersihkan_internal()
            path = self._metadata_path(photo_id)
            if path.exists():
                return photo_id
            payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            check_capacity(self.root, len(data) + len(payload), "DATASET_PENDING_MAX_MB", 512)
            image_path = self.root / f"{photo_id}.img"
            self._tulis_atomik(image_path, data)
            try:
                self._tulis_atomik(path, payload)
            except Exception:
                image_path.unlink(missing_ok=True)
                raise
        return photo_id

    def get_pending_image(self, photo_id: str) -> bytes:
        validasi_id(photo_id)
        img_path = self.root / f"{photo_id}.img"
        if not img_path.exists():
            raise FileNotFoundError(f"Foto pending {photo_id} tidak ditemukan di disk lokal")
        if img_path.is_symlink():
            raise ValueError("Symlink tidak diizinkan")
        return img_path.read_bytes()

    def get_pending_metadata(self, photo_id: str) -> Dict[str, Any]:
        path = self._metadata_path(photo_id)
        if not path.exists():
            raise FileNotFoundError(f"Metadata pending {photo_id} tidak ditemukan")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_pending(self, jenis: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json")):
            if not ID_PATTERN.fullmatch(path.stem) or path.is_symlink():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status_review") != "pending":
                    continue
                prediksi = record.get("prediksi_sementara") or {}
                if jenis is not None and normalisasi(prediksi.get("jenis")) != normalisasi(jenis):
                    continue
                record["foto"] = str(path.with_suffix(".img").resolve())
                record["storage_backend"] = "local"
                records.append(record)
            except Exception:
                continue
        return records

    def mark_verified(self, photo_id: str, verified_record: Dict[str, Any]) -> None:
        import fcntl
        with self._lock() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self._metadata_path(photo_id)
            payload = json.dumps(verified_record, ensure_ascii=False, indent=2).encode("utf-8")
            check_capacity(self.root, len(payload), "DATASET_PENDING_MAX_MB", 512)
            self._tulis_atomik(path, payload)

    def _bersihkan_internal(self, retention_days: int = 30, dry_run: bool = False, now: Optional[datetime] = None) -> List[str]:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
        removed = []
        if not self.root.exists():
            return removed
        for path in self.root.glob("*.json"):
            if not ID_PATTERN.fullmatch(path.stem) or path.is_symlink():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(record["created_at"])
                if record.get("status_review") != "pending" or created >= cutoff:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            image = path.with_suffix(".img")
            if image.is_symlink():
                continue
            removed.append(path.stem)
            if not dry_run:
                image.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
        return removed

    def cleanup_expired(self, retention_days: int = 30, dry_run: bool = False) -> Dict[str, Any]:
        import fcntl
        with self._lock() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            removed = self._bersihkan_internal(retention_days=retention_days, dry_run=dry_run)
            return {
                "id_kedaluwarsa": removed,
                "dry_run": dry_run,
                "bytes_tersimpan": usage(self.root) if self.root.exists() else 0,
            }


# ============================================================================
# 2. SUPABASE STORAGE & DATABASE BACKEND
# ============================================================================

class SupabaseStorageBackend(StorageBackend):
    """
    Penyimpanan cloud menggunakan Supabase:
    - Object Storage bucket untuk berkas foto (.img) dan cadangan metadata (.json).
    - PostgreSQL table (PostgREST) untuk metadata terstruktur (opsional, fallback otomatis ke storage jika tabel belum ada).
    """

    def __init__(
        self,
        url: str,
        key: str,
        bucket: str = "pending",
        table: str = "pending_scans",
        client: Optional[httpx.Client] = None,
    ):
        # Bersihkan suffix seperti /rest/v1 atau /rest jika pengguna menyalin endpoint REST
        cleaned_url = re.sub(r"/rest(/v1)?/?$", "", url.strip().rstrip("/"))
        self.url = cleaned_url
        self.key = key
        self.bucket = bucket
        self.table = table
        self._client = client

    def is_online(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "tipe": "supabase",
            "url": self.url,
            "bucket": self.bucket,
            "table": self.table,
            "online": True,
        }

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {"apikey": self.key}
        # Supabase's newer opaque API keys (sb_secret_*/sb_publishable_*) are
        # accepted via apikey only. Sending one as a Bearer token makes the
        # gateway try to parse it as a JWT and reject it. Legacy JWT keys still
        # need the Authorization header for Storage and PostgREST requests.
        if not self.key.startswith(("sb_secret_", "sb_publishable_")):
            headers["Authorization"] = f"Bearer {self.key}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _get_client(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=15.0)

    def save_pending(self, photo_id: str, data: bytes, record: Dict[str, Any]) -> str:
        validasi_id(photo_id)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError(f"Foto melebihi batas maksimal {MAX_PHOTO_BYTES} byte")

        client = self._get_client()
        should_close = self._client is None

        try:
            # 1. Simpan foto ke Storage Bucket (POST /storage/v1/object/{bucket}/{filename})
            img_path = f"{photo_id}.img"
            upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{img_path}"
            headers = self._headers("image/octet-stream")
            headers["x-upsert"] = "true"
            res = client.post(upload_url, headers=headers, content=data)
            if res.status_code not in (200, 201):
                # Beberapa konfigurasi Supabase Storage menggunakan PUT jika file sudah ada
                res = client.put(upload_url, headers=headers, content=data)
            if res.status_code not in (200, 201):
                raise RuntimeError(f"Gagal mengunggah foto ke Supabase Storage ({res.status_code}): {res.text[:120]}")

            # 2. Simpan metadata JSON ke Storage Bucket sebagai cadangan persisten
            json_path = f"{photo_id}.json"
            meta_payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            meta_url = f"{self.url}/storage/v1/object/{self.bucket}/{json_path}"
            meta_headers = self._headers("application/json")
            meta_headers["x-upsert"] = "true"
            res_meta = client.post(meta_url, headers=meta_headers, content=meta_payload)
            if res_meta.status_code not in (200, 201):
                res_meta = client.put(meta_url, headers=meta_headers, content=meta_payload)

            # 3. Coba simpan metadata ke PostgreSQL Table via PostgREST
            try:
                table_url = f"{self.url}/rest/v1/{self.table}"
                db_headers = self._headers("application/json")
                db_headers["Prefer"] = "resolution=merge-duplicates"
                db_record = {
                    "id": photo_id,
                    "status_review": "pending",
                    "created_at": record.get("created_at"),
                    "request_id": record.get("request_id"),
                    "prediksi_sementara": record.get("prediksi_sementara"),
                    "foto_path": f"{self.bucket}/{img_path}",
                }
                client.post(table_url, headers=db_headers, json=db_record)
            except Exception as db_exc:
                # Jika tabel belum dibuat, penyimpanan di storage bucket tetap aman
                logger.debug("Supabase DB table upsert dilewati / gagal: %s", db_exc)

            return photo_id
        finally:
            if should_close:
                client.close()

    def get_pending_image(self, photo_id: str) -> bytes:
        validasi_id(photo_id)
        client = self._get_client()
        should_close = self._client is None
        try:
            # Coba authenticated URL terlebih dahulu
            url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{photo_id}.img"
            res = client.get(url, headers=self._headers())
            if res.status_code == 404 or res.status_code >= 400:
                # Coba public URL
                public_url = f"{self.url}/storage/v1/object/public/{self.bucket}/{photo_id}.img"
                res = client.get(public_url, headers=self._headers())
            if res.status_code != 200:
                raise FileNotFoundError(f"Foto {photo_id} tidak ditemukan di Supabase Storage ({res.status_code})")
            return res.content
        finally:
            if should_close:
                client.close()

    def get_pending_metadata(self, photo_id: str) -> Dict[str, Any]:
        validasi_id(photo_id)
        client = self._get_client()
        should_close = self._client is None
        try:
            # 1. Coba dari tabel PostgREST
            try:
                table_url = f"{self.url}/rest/v1/{self.table}?id=eq.{photo_id}&select=*"
                res = client.get(table_url, headers=self._headers())
                if res.status_code == 200:
                    rows = res.json()
                    if rows:
                        row = rows[0]
                        return {
                            "id": row.get("id"),
                            "status_review": row.get("status_review", "pending"),
                            "created_at": row.get("created_at"),
                            "request_id": row.get("request_id"),
                            "prediksi_sementara": row.get("prediksi_sementara") or {},
                            "label_manusia": row.get("label_manusia"),
                            "verified_at": row.get("verified_at"),
                            "dataset": row.get("dataset"),
                        }
            except Exception:
                pass

            # 2. Fallback ke file JSON di Storage Bucket
            url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{photo_id}.json"
            res = client.get(url, headers=self._headers())
            if res.status_code != 200:
                public_url = f"{self.url}/storage/v1/object/public/{self.bucket}/{photo_id}.json"
                res = client.get(public_url, headers=self._headers())
            if res.status_code != 200:
                raise FileNotFoundError(f"Metadata {photo_id} tidak ditemukan di Supabase ({res.status_code})")
            return json.loads(res.text)
        finally:
            if should_close:
                client.close()

    def list_pending(self, jenis: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self._get_client()
        should_close = self._client is None
        records: List[Dict[str, Any]] = []
        try:
            # 1. Coba baca dari tabel PostgREST
            used_table = False
            try:
                table_url = f"{self.url}/rest/v1/{self.table}?status_review=eq.pending&order=created_at.desc"
                res = client.get(table_url, headers=self._headers())
                if res.status_code == 200:
                    rows = res.json()
                    for r in rows:
                        prediksi = r.get("prediksi_sementara") or {}
                        if jenis is not None and normalisasi(prediksi.get("jenis")) != normalisasi(jenis):
                            continue
                        records.append({
                            "id": r.get("id"),
                            "status_review": "pending",
                            "created_at": r.get("created_at"),
                            "request_id": r.get("request_id"),
                            "prediksi_sementara": prediksi,
                            "foto": f"supabase://{self.bucket}/{r.get('id')}.img",
                            "storage_backend": "supabase",
                        })
                    used_table = True
            except Exception:
                used_table = False

            if used_table:
                return records

            # 2. Fallback: scan Storage Bucket
            list_url = f"{self.url}/storage/v1/object/list/{self.bucket}"
            body = {"prefix": "", "limit": 1000, "sortBy": {"column": "created_at", "order": "desc"}}
            res = client.post(list_url, headers=self._headers("application/json"), json=body)
            if res.status_code != 200:
                return []
            items = res.json()
            for item in items:
                name = item.get("name", "")
                if name.endswith(".json") and ID_PATTERN.fullmatch(name[:-5]):
                    identitas = name[:-5]
                    try:
                        meta = self.get_pending_metadata(identitas)
                        if meta.get("status_review") != "pending":
                            continue
                        prediksi = meta.get("prediksi_sementara") or {}
                        if jenis is not None and normalisasi(prediksi.get("jenis")) != normalisasi(jenis):
                            continue
                        meta["foto"] = f"supabase://{self.bucket}/{identitas}.img"
                        meta["storage_backend"] = "supabase"
                        records.append(meta)
                    except Exception:
                        continue
            return records
        finally:
            if should_close:
                client.close()

    def mark_verified(self, photo_id: str, verified_record: Dict[str, Any]) -> None:
        validasi_id(photo_id)
        client = self._get_client()
        should_close = self._client is None
        try:
            # 1. Perbarui file JSON di Storage Bucket
            json_path = f"{photo_id}.json"
            payload = json.dumps(verified_record, ensure_ascii=False, indent=2).encode("utf-8")
            url = f"{self.url}/storage/v1/object/{self.bucket}/{json_path}"
            headers = self._headers("application/json")
            headers["x-upsert"] = "true"
            client.put(url, headers=headers, content=payload)

            # 2. Perbarui PostgREST table jika ada
            try:
                table_url = f"{self.url}/rest/v1/{self.table}?id=eq.{photo_id}"
                patch_data = {
                    "status_review": "verified",
                    "verified_at": verified_record.get("verified_at"),
                    "label_manusia": verified_record.get("label_manusia"),
                    "dataset": verified_record.get("dataset"),
                }
                client.patch(table_url, headers=self._headers("application/json"), json=patch_data)
            except Exception:
                pass
        finally:
            if should_close:
                client.close()

    def cleanup_expired(self, retention_days: int = 30, dry_run: bool = False) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        pending_list = self.list_pending()
        expired_ids = []
        for r in pending_list:
            try:
                created = datetime.fromisoformat(r["created_at"])
                if created < cutoff:
                    expired_ids.append(r["id"])
            except Exception:
                continue

        if dry_run or not expired_ids:
            return {"id_kedaluwarsa": expired_ids, "dry_run": dry_run, "backend": "supabase"}

        client = self._get_client()
        should_close = self._client is None
        try:
            prefixes = []
            for eid in expired_ids:
                prefixes.extend([f"{eid}.img", f"{eid}.json"])
            del_url = f"{self.url}/storage/v1/object/{self.bucket}"
            client.request("DELETE", del_url, headers=self._headers("application/json"), json={"prefixes": prefixes})

            # Hapus dari tabel DB jika ada
            try:
                ids_param = f"in.({','.join(expired_ids)})"
                table_url = f"{self.url}/rest/v1/{self.table}?id={ids_param}"
                client.delete(table_url, headers=self._headers())
            except Exception:
                pass

            return {"id_kedaluwarsa": expired_ids, "dry_run": False, "backend": "supabase"}
        finally:
            if should_close:
                client.close()


# ============================================================================
# 3. S3 / CLOUDFLARE R2 BACKEND
# ============================================================================

def sign_aws_v4(
    method: str,
    url: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str = "auto",
    service: str = "s3",
    content_type: Optional[str] = None,
) -> Dict[str, str]:
    """Menghasilkan header AWS Signature Version 4 dalam pure Python."""
    parsed = urllib.parse.urlsplit(url)
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": parsed.netloc,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if content_type:
        headers["content-type"] = content_type

    sorted_header_names = sorted(headers.keys())
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted_header_names)
    signed_headers = ";".join(sorted_header_names)

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    canonical_query = urllib.parse.urlencode(sorted(query_params))

    canonical_request = (
        f"{method}\n{parsed.path}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers["Authorization"] = auth_header
    return headers


class S3StorageBackend(StorageBackend):
    """
    Penyimpanan cloud object storage kompatibel S3 (Cloudflare R2, AWS S3, MinIO, Wasabi).
    Mendukung Cloudflare R2 tanpa biaya transfer (egress).
    """

    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = None,
        region: str = "auto",
        prefix: str = "pending/",
        client: Optional[httpx.Client] = None,
    ):
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint_url = (endpoint_url or f"https://{bucket}.s3.{region}.amazonaws.com").rstrip("/")
        self.region = region
        self.prefix = prefix.strip("/") + ("/" if prefix.strip("/") else "")
        self._client = client

    def is_online(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "tipe": "s3",
            "bucket": self.bucket,
            "prefix": self.prefix,
            "region": self.region,
            "online": True,
        }

    def _object_url(self, key: str) -> str:
        # Menangani path-style (endpoint_url/bucket/key) atau virtual-hosted
        if self.bucket in self.endpoint_url:
            return f"{self.endpoint_url}/{key}"
        return f"{self.endpoint_url}/{self.bucket}/{key}"

    def _request(self, method: str, key: str, body: bytes = b"", content_type: Optional[str] = None, query: str = "") -> httpx.Response:
        url = self._object_url(key)
        if query:
            url += f"?{query}"
        headers = sign_aws_v4(
            method=method,
            url=url,
            body=body,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            service="s3",
            content_type=content_type,
        )
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            return client.request(method, url, headers=headers, content=body)
        finally:
            if should_close:
                client.close()

    def save_pending(self, photo_id: str, data: bytes, record: Dict[str, Any]) -> str:
        validasi_id(photo_id)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError(f"Foto melebihi batas maksimal {MAX_PHOTO_BYTES} byte")
        # 1. Simpan foto .img
        img_key = f"{self.prefix}{photo_id}.img"
        res_img = self._request("PUT", img_key, body=data, content_type="image/octet-stream")
        if res_img.status_code not in (200, 201):
            raise RuntimeError(f"Gagal mengunggah foto ke S3 ({res_img.status_code}): {res_img.text[:120]}")

        # 2. Simpan metadata .json
        json_key = f"{self.prefix}{photo_id}.json"
        meta_payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        res_meta = self._request("PUT", json_key, body=meta_payload, content_type="application/json")
        if res_meta.status_code not in (200, 201):
            raise RuntimeError(f"Gagal mengunggah metadata ke S3 ({res_meta.status_code})")

        return photo_id

    def get_pending_image(self, photo_id: str) -> bytes:
        validasi_id(photo_id)
        img_key = f"{self.prefix}{photo_id}.img"
        res = self._request("GET", img_key)
        if res.status_code != 200:
            raise FileNotFoundError(f"Foto {photo_id} tidak ditemukan di S3/R2 ({res.status_code})")
        return res.content

    def get_pending_metadata(self, photo_id: str) -> Dict[str, Any]:
        validasi_id(photo_id)
        json_key = f"{self.prefix}{photo_id}.json"
        res = self._request("GET", json_key)
        if res.status_code != 200:
            raise FileNotFoundError(f"Metadata {photo_id} tidak ditemukan di S3/R2 ({res.status_code})")
        return json.loads(res.text)

    def list_pending(self, jenis: Optional[str] = None) -> List[Dict[str, Any]]:
        query = f"list-type=2&prefix={self.prefix}"
        res = self._request("GET", "", query=query)
        if res.status_code != 200:
            return []
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(res.text)
        except Exception:
            return []

        records = []
        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1]
            if tag_name == "Key" and elem.text:
                key = elem.text.strip()
                if key.endswith(".json"):
                    stem = key[len(self.prefix):-5] if key.startswith(self.prefix) else key[:-5]
                    if not ID_PATTERN.fullmatch(stem):
                        continue
                    try:
                        meta = self.get_pending_metadata(stem)
                        if meta.get("status_review") != "pending":
                            continue
                        prediksi = meta.get("prediksi_sementara") or {}
                        if jenis is not None and normalisasi(prediksi.get("jenis")) != normalisasi(jenis):
                            continue
                        meta["foto"] = f"s3://{self.bucket}/{self.prefix}{stem}.img"
                        meta["storage_backend"] = "s3"
                        records.append(meta)
                    except Exception:
                        continue
        return records

    def mark_verified(self, photo_id: str, verified_record: Dict[str, Any]) -> None:
        validasi_id(photo_id)
        json_key = f"{self.prefix}{photo_id}.json"
        meta_payload = json.dumps(verified_record, ensure_ascii=False, indent=2).encode("utf-8")
        self._request("PUT", json_key, body=meta_payload, content_type="application/json")

    def cleanup_expired(self, retention_days: int = 30, dry_run: bool = False) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        pending_list = self.list_pending()
        expired_ids = []
        for r in pending_list:
            try:
                created = datetime.fromisoformat(r["created_at"])
                if created < cutoff:
                    expired_ids.append(r["id"])
            except Exception:
                continue

        if dry_run or not expired_ids:
            return {"id_kedaluwarsa": expired_ids, "dry_run": dry_run, "backend": "s3"}

        for eid in expired_ids:
            self._request("DELETE", f"{self.prefix}{eid}.img")
            self._request("DELETE", f"{self.prefix}{eid}.json")

        return {"id_kedaluwarsa": expired_ids, "dry_run": False, "backend": "s3"}


# ============================================================================
# 4. VERCEL BLOB STORAGE BACKEND
# ============================================================================

class VercelBlobStorageBackend(StorageBackend):
    """Penyimpanan bawaan Vercel Blob via HTTP REST API."""

    def __init__(
        self,
        token: str,
        prefix: str = "pending/",
        client: Optional[httpx.Client] = None,
    ):
        self.token = token
        self.prefix = prefix.strip("/") + ("/" if prefix.strip("/") else "")
        self._client = client

    def is_online(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "tipe": "vercel_blob",
            "prefix": self.prefix,
            "online": True,
        }

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def save_pending(self, photo_id: str, data: bytes, record: Dict[str, Any]) -> str:
        validasi_id(photo_id)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError(f"Foto melebihi batas maksimal {MAX_PHOTO_BYTES} byte")
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            # 1. Upload img
            img_url = f"https://blob.vercel-storage.com/{self.prefix}{photo_id}.img"
            headers = self._headers("image/octet-stream")
            headers["x-add-random-suffix"] = "false"
            res_img = client.put(img_url, headers=headers, content=data)
            if res_img.status_code not in (200, 201):
                raise RuntimeError(f"Gagal mengunggah foto ke Vercel Blob ({res_img.status_code})")

            # 2. Upload json
            json_url = f"https://blob.vercel-storage.com/{self.prefix}{photo_id}.json"
            meta_headers = self._headers("application/json")
            meta_headers["x-add-random-suffix"] = "false"
            payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
            res_meta = client.put(json_url, headers=meta_headers, content=payload)
            if res_meta.status_code not in (200, 201):
                raise RuntimeError(f"Gagal mengunggah metadata ke Vercel Blob ({res_meta.status_code})")

            return photo_id
        finally:
            if should_close:
                client.close()

    def _find_blob_url(self, pathname: str) -> Optional[str]:
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            res = client.get(
                f"https://blob.vercel-storage.com?prefix={pathname}&limit=1",
                headers=self._headers(),
            )
            if res.status_code == 200:
                blobs = res.json().get("blobs", [])
                for b in blobs:
                    if b.get("pathname") == pathname:
                        return b.get("url")
            return None
        finally:
            if should_close:
                client.close()

    def get_pending_image(self, photo_id: str) -> bytes:
        validasi_id(photo_id)
        pathname = f"{self.prefix}{photo_id}.img"
        url = self._find_blob_url(pathname)
        if not url:
            raise FileNotFoundError(f"Foto {photo_id} tidak ditemukan di Vercel Blob")
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            res = client.get(url)
            if res.status_code != 200:
                raise FileNotFoundError(f"Gagal mengunduh foto {photo_id} ({res.status_code})")
            return res.content
        finally:
            if should_close:
                client.close()

    def get_pending_metadata(self, photo_id: str) -> Dict[str, Any]:
        validasi_id(photo_id)
        pathname = f"{self.prefix}{photo_id}.json"
        url = self._find_blob_url(pathname)
        if not url:
            raise FileNotFoundError(f"Metadata {photo_id} tidak ditemukan di Vercel Blob")
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            res = client.get(url)
            if res.status_code != 200:
                raise FileNotFoundError(f"Gagal mengunduh metadata {photo_id} ({res.status_code})")
            return json.loads(res.text)
        finally:
            if should_close:
                client.close()

    def list_pending(self, jenis: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            res = client.get(
                f"https://blob.vercel-storage.com?prefix={self.prefix}&limit=1000",
                headers=self._headers(),
            )
            if res.status_code != 200:
                return []
            blobs = res.json().get("blobs", [])
            records = []
            for b in blobs:
                pathname = b.get("pathname", "")
                if pathname.endswith(".json"):
                    stem = pathname[len(self.prefix):-5]
                    if not ID_PATTERN.fullmatch(stem):
                        continue
                    try:
                        dl = client.get(b.get("url"))
                        if dl.status_code != 200:
                            continue
                        meta = json.loads(dl.text)
                        if meta.get("status_review") != "pending":
                            continue
                        prediksi = meta.get("prediksi_sementara") or {}
                        if jenis is not None and normalisasi(prediksi.get("jenis")) != normalisasi(jenis):
                            continue
                        meta["foto"] = b.get("url")
                        meta["storage_backend"] = "vercel_blob"
                        records.append(meta)
                    except Exception:
                        continue
            return records
        finally:
            if should_close:
                client.close()

    def mark_verified(self, photo_id: str, verified_record: Dict[str, Any]) -> None:
        validasi_id(photo_id)
        client = self._client or httpx.Client(timeout=15.0)
        should_close = self._client is None
        try:
            json_url = f"https://blob.vercel-storage.com/{self.prefix}{photo_id}.json"
            meta_headers = self._headers("application/json")
            meta_headers["x-add-random-suffix"] = "false"
            payload = json.dumps(verified_record, ensure_ascii=False, indent=2).encode("utf-8")
            client.put(json_url, headers=meta_headers, content=payload)
        finally:
            if should_close:
                client.close()

    def cleanup_expired(self, retention_days: int = 30, dry_run: bool = False) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        pending_list = self.list_pending()
        expired_ids = []
        expired_urls = []
        for r in pending_list:
            try:
                created = datetime.fromisoformat(r["created_at"])
                if created < cutoff:
                    expired_ids.append(r["id"])
                    expired_urls.append(r["foto"])
                    json_url = self._find_blob_url(f"{self.prefix}{r['id']}.json")
                    if json_url:
                        expired_urls.append(json_url)
            except Exception:
                continue

        if dry_run or not expired_ids:
            return {"id_kedaluwarsa": expired_ids, "dry_run": dry_run, "backend": "vercel_blob"}

        client = self._get_client() if hasattr(self, "_get_client") else (self._client or httpx.Client(timeout=15.0))
        should_close = self._client is None
        try:
            client.post(
                "https://blob.vercel-storage.com/delete",
                headers=self._headers("application/json"),
                json={"urls": expired_urls},
            )
            return {"id_kedaluwarsa": expired_ids, "dry_run": False, "backend": "vercel_blob"}
        finally:
            if should_close:
                client.close()


# ============================================================================
# FACTORY SELEKSI PENYIMPANAN
# ============================================================================

def get_storage_backend(
    root: Optional[Path] = None,
    backend_type: Optional[str] = None,
) -> StorageBackend:
    """
    Factory untuk memilih storage backend berdasarkan konfigurasi:
    1. Jika backend_type diberikan secara eksplisit, gunakan tipe itu.
    2. Jika root diberikan dan tidak ada tipe online eksplisit, gunakan LocalStorageBackend(root).
    3. Jika tidak, periksa variabel lingkungan (STORAGE_BACKEND, SUPABASE_*, BLOB_*, S3_*).
    4. Default: LocalStorageBackend.
    """
    tipe = (backend_type or os.getenv("STORAGE_BACKEND", "")).lower().strip()

    # Jika root diberikan tanpa spesifikasi online backend, utamakan direktori lokal tersebut
    if root is not None and not backend_type and not os.getenv("STORAGE_BACKEND"):
        return LocalStorageBackend(root=Path(root))

    if tipe == "local":
        from backend.app.services.dataset_pending import DEFAULT_DIR, direktori
        target_root = Path(root) if root is not None else direktori()
        return LocalStorageBackend(root=target_root)

    # 1. Supabase
    if tipe == "supabase" or (not tipe and os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY")):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL dan SUPABASE_KEY wajib diset untuk backend Supabase")
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET") or os.getenv("SUPABASE_BUCKET", "pending")
        table = os.getenv("SUPABASE_TABLE", "pending_scans")
        return SupabaseStorageBackend(url=url, key=key, bucket=bucket, table=table)

    # 2. Vercel Blob
    if tipe in ("vercel_blob", "blob") or (not tipe and os.getenv("BLOB_READ_WRITE_TOKEN")):
        token = os.getenv("BLOB_READ_WRITE_TOKEN")
        if not token:
            raise ValueError("BLOB_READ_WRITE_TOKEN wajib diset untuk backend Vercel Blob")
        prefix = os.getenv("BLOB_PREFIX", "pending/")
        return VercelBlobStorageBackend(token=token, prefix=prefix)

    # 3. S3 / Cloudflare R2
    if tipe in ("s3", "r2") or (not tipe and (os.getenv("S3_BUCKET") or os.getenv("R2_BUCKET") or os.getenv("AWS_S3_BUCKET"))):
        bucket = os.getenv("S3_BUCKET") or os.getenv("R2_BUCKET") or os.getenv("AWS_S3_BUCKET")
        access_key = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
        endpoint_url = os.getenv("S3_ENDPOINT_URL") or os.getenv("R2_ENDPOINT_URL")
        region = os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION", "auto")
        prefix = os.getenv("S3_PREFIX", "pending/")
        if not bucket or not access_key or not secret_key:
            raise ValueError("S3_BUCKET, S3_ACCESS_KEY_ID, dan S3_SECRET_ACCESS_KEY wajib diset untuk backend S3")
        return S3StorageBackend(
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            endpoint_url=endpoint_url,
            region=region,
            prefix=prefix,
        )

    # 4. Default: Local
    from backend.app.services.dataset_pending import DEFAULT_DIR, direktori
    target_root = Path(root) if root is not None else direktori()
    return LocalStorageBackend(root=target_root)
