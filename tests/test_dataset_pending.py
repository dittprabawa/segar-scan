import io
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app import main
from backend.app.services import dataset_pending as pending
from scripts.evaluasi_jenis import baca_dataset


def foto():
    buffer = io.BytesIO()
    Image.new('RGB', (30, 30), 'green').save(buffer, format='PNG')
    return buffer.getvalue()


def test_duplikat_konkuren_dan_verifikasi(tmp_path):
    root = tmp_path / 'pending'
    data = foto()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: pending.simpan(data, {'jenis': 'bayam'}, 'req', root), range(8)))
    assert len(set(ids)) == 1
    assert len(list(root.glob('*.json'))) == 1
    dataset = tmp_path / 'verified' / 'dataset.csv'
    assert not dataset.exists()
    pending.verifikasi(ids[0], 'caisim', 'ok', 'HP', 'test', dataset, root)
    assert baca_dataset(dataset, 'test')[0]['jenis_benar'] == 'caisim'
    pending.simpan(data, {'jenis': 'bayam'}, 'req2', root)
    record = json.loads((root / (ids[0] + '.json')).read_text())
    assert record['status_review'] == 'verified'
    with pytest.raises(ValueError, match='sudah diverifikasi'):
        pending.verifikasi(ids[0], 'bayam', 'ok', 'HP', 'dev', dataset, root)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('DATASET_PENDING_DIR', str(tmp_path / 'pending'))
    monkeypatch.setenv('DATASET_CAPTURE', '1')
    monkeypatch.setattr(main.limiter, 'enabled', False)
    service = Mock(siap=True, model=None)
    service.predict.return_value = {'status': 'ok', 'label': 'Segar', 'jenis': 'bayam'}
    monkeypatch.setattr(main, 'service', service)
    return TestClient(main.app), service, tmp_path / 'pending'


@pytest.mark.parametrize('status', ['ok', 'ditolak', 'error'])
def test_upload_otomatis(api, status):
    client, service, root = api
    if status == 'error':
        service.predict.side_effect = RuntimeError('gagal')
    else:
        service.predict.return_value['status'] = status
    response = client.post('/predict', files={'file': ('foto.png', foto(), 'image/png')})
    assert response.status_code == (503 if status == 'error' else 200)
    record = json.loads(next(root.glob('*.json')).read_text())
    assert record['status_review'] == 'pending'
    assert record['prediksi_sementara']['status'] == status
    assert record['request_id'] == response.headers['x-request-id']
    assert next(root.glob('*.img')).read_bytes() == foto()


def test_invalid_dan_capture_nonaktif(api, monkeypatch):
    client, service, root = api
    assert client.post('/predict', files={'file': ('x', b'invalid')}).status_code == 400
    assert not root.exists()
    monkeypatch.setenv('DATASET_CAPTURE', '0')
    assert client.post('/predict', files={'file': ('x', foto())}).status_code == 200
    assert not root.exists()


def test_disk_gagal_tetap_prediksi(api, monkeypatch):
    client, service, root = api
    monkeypatch.setattr(pending, 'simpan', Mock(side_effect=OSError('disk penuh')))
    assert client.post('/predict', files={'file': ('x', foto())}).status_code == 200


def test_id_tidak_boleh_path_traversal(tmp_path):
    with pytest.raises(ValueError, match='ID foto'):
        pending.verifikasi('../x', 'bayam', 'ok', 'HP', 'dev', tmp_path / 'dataset.csv', tmp_path)


def test_daging_api_pending_dan_verifikasi(api, tmp_path):
    client, service, root = api
    service.predict.return_value.update(jenis='daging ayam', label='Tampilan relatif baik')
    response = client.post('/predict', files={'file': ('ayam.png', foto(), 'image/png')})
    assert response.status_code == 200
    assert response.json()['jenis'] == 'daging ayam'
    record = json.loads(next(root.glob('*.json')).read_text())
    dataset = tmp_path / 'verified' / 'dataset.csv'
    pending.verifikasi(record['id'], 'daging ayam', 'ok', 'HP', 'dev', dataset, root)
    assert baca_dataset(dataset, 'dev')[0]['jenis_benar'] == 'daging ayam'


def test_api_menyertakan_ml_lokal_tanpa_mengubah_skor(api, monkeypatch):
    client, service, root = api
    candidate = {'jenis': 'tomat', 'metode': 'jenis-knn-v1', 'jarak': 0.2,
                 'eksperimental': True, 'jumlah_latih': 18}
    monkeypatch.setattr(main.jenis_lokal, 'predict', lambda image: candidate)
    service.predict.return_value['skor'] = 85
    response = client.post('/predict', files={'file': ('foto.png', foto(), 'image/png')})
    result = response.json()
    assert result['jenis'] == 'bayam'
    assert result['skor'] == 85
    assert result['jenis_lokal'] == candidate


def test_model_lokal_rusak_tidak_memutus_prediksi(api, monkeypatch):
    client, service, root = api
    monkeypatch.setattr(main.jenis_lokal, 'predict', Mock(side_effect=ValueError('rusak')))
    assert client.post('/predict', files={'file': ('foto.png', foto())}).status_code == 200
