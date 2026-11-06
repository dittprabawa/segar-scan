import asyncio
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app.main import app, LimitUploadSizeMiddleware, MAKS_BYTES
from backend.app.services import dataset_pending as pending
from backend.app.services.storage_policy import StorageFull
from scripts.update_dataset import update_dataset


def test_retensi_mempertahankan_verified(tmp_path):
    old = pending.simpan(b'old', {}, 'req', tmp_path)
    verified = pending.simpan(b'verified', {}, 'req', tmp_path)
    recent = pending.simpan(b'recent', {}, 'req', tmp_path)
    for ident in (old, verified):
        path = tmp_path / (ident + '.json')
        record = json.loads(path.read_text())
        record['created_at'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        if ident == verified:
            record['status_review'] = 'verified'
        path.write_text(json.dumps(record))
    assert pending.bersihkan(tmp_path, dry_run=True)['id_kedaluwarsa'] == [old]
    assert (tmp_path / (old + '.img')).exists()
    assert pending.bersihkan(tmp_path)['id_kedaluwarsa'] == [old]
    assert not (tmp_path / (old + '.img')).exists()
    for ident in (verified, recent):
        assert (tmp_path / (ident + '.img')).exists()


def test_kuota_konkuren_dan_deduplikasi(tmp_path, monkeypatch):
    monkeypatch.setenv('DATASET_PENDING_MAX_MB', '1')
    def save(i):
        try:
            return pending.simpan(bytes([i]) * 700000, {}, 'req', tmp_path)
        except StorageFull:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sum(x is not None for x in results) == 1
    assert len(list(tmp_path.glob('*.img'))) == 1
    idx = 1 if results[0] else 2
    assert pending.simpan(bytes([idx]) * 700000, {}, 'req', tmp_path) in results


def test_verified_quota_does_not_create_partial_data(tmp_path, monkeypatch):
    monkeypatch.setenv('DATASET_VERIFIED_MAX_MB', '1')
    photo = tmp_path / 'large.png'
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (700, 700, 3), dtype=np.uint8)).save(photo)
    dataset = tmp_path / 'verified' / 'dataset.csv'
    with pytest.raises(StorageFull):
        update_dataset(photo, 'bayam', 'ok', 'HP', dataset=dataset)
    assert not dataset.exists()
    assert not list((dataset.parent / 'images').glob('*'))


def test_symlink_pending_preserved(tmp_path):
    outside = tmp_path / 'private.txt'
    outside.write_text('private')
    root = tmp_path / 'pending'
    root.mkdir()
    import hashlib
    ident = hashlib.sha256(b'data').hexdigest()
    (root / (ident + '.img')).symlink_to(outside)
    with pytest.raises(ValueError, match='Symlink'):
        pending.simpan(b'data', {}, 'req', root)
    assert outside.read_text() == 'private'


@pytest.mark.parametrize('length', [None, b'1'])
def test_stream_body_limit_before_parser(length):
    reached = []
    messages = []
    chunks = iter([{'type': 'http.request', 'body': b'x' * (MAKS_BYTES // 2), 'more_body': True}] * 3)
    async def downstream(scope, receive, send):
        reached.append(True)
    async def receive():
        return next(chunks)
    async def send(message):
        messages.append(message)
    headers = [] if length is None else [(b'content-length', length)]
    asyncio.run(LimitUploadSizeMiddleware(downstream)(
        {'type': 'http', 'method': 'POST', 'path': '/predict', 'headers': headers}, receive, send))
    assert not reached
    assert messages[0]['status'] == 413


def test_private_files_not_public_and_cors():
    client = TestClient(app)
    for path in ['/.env', '/data/pending/', '/scripts/dataset.csv', '/ui/../.env', '/ui/%2e%2e/.env']:
        assert client.get(path).status_code == 404
    result = client.options('/predict', headers={'Origin': 'https://untrusted.invalid',
                                                 'Access-Control-Request-Method': 'POST'})
    assert 'access-control-allow-origin' not in result.headers


def test_validation_input_not_logged(caplog):
    client = TestClient(app)
    secret = 'private-upload-field-content'
    result = client.post('/predict', data={'file': secret})
    assert result.status_code == 422
    assert secret not in caplog.text
