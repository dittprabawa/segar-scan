import io
import json

from PIL import Image
import pytest

from backend.app.services.dataset_pending import simpan
from scripts.review_dataset import daftar_pending, verifikasi_grup
from scripts.evaluasi_jenis import baca_dataset


def tambah(root, jenis, warna):
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), warna).save(buffer, format='PNG')
    return simpan(buffer.getvalue(), {'jenis': jenis, 'status': 'ok'}, 'tes', root)


def test_filter_pratinjau_koreksi_dan_exclude(tmp_path):
    root = tmp_path / 'pending'
    ayam = tambah(root, 'Daging  Ayam', 'red')
    skip = tambah(root, 'daging ayam', 'blue')
    sapi = tambah(root, 'daging sapi', 'green')
    dataset = tmp_path / 'dataset.csv'
    args = (root, 'daging ayam', 'ok', 'Macbook', 'dev', dataset)
    preview = verifikasi_grup(*args, exclude=[skip], dry_run=True)
    assert preview['dipilih'] == [ayam]
    assert not dataset.exists()
    result = verifikasi_grup(*args, label='daging ayam', exclude=[skip])
    assert result['berhasil'] == [ayam]
    assert not result['gagal']
    assert baca_dataset(dataset, 'dev')[0]['jenis_benar'] == 'daging ayam'
    assert {r['id'] for r in daftar_pending(root)} == {skip, sapi}
    assert json.loads((root / (ayam + '.json')).read_text())['status_review'] == 'verified'


def test_koreksi_kelompok_dan_ulang_tanpa_duplikat(tmp_path):
    root = tmp_path / 'pending'
    for warna in ['red', 'green']:
        tambah(root, 'tomat', warna)
    dataset = tmp_path / 'dataset.csv'
    args = (root, 'tomat', 'ok', 'HP', 'test', dataset)
    assert len(verifikasi_grup(*args, label='bayam')['berhasil']) == 2
    assert verifikasi_grup(*args)['dipilih'] == []
    assert [r['jenis_benar'] for r in baca_dataset(dataset, 'test')] == ['bayam', 'bayam']


def test_exclude_salah_tidak_mengubah_data(tmp_path):
    tambah(tmp_path, 'wortel', 'red')
    dataset = tmp_path / 'dataset.csv'
    with pytest.raises(ValueError, match='pengecualian'):
        verifikasi_grup(tmp_path, 'wortel', 'ok', 'HP', 'dev', dataset, exclude=['salah'])
    assert not dataset.exists()


def test_gagal_sebagian_dilaporkan(tmp_path):
    good = tambah(tmp_path, 'bayam', 'red')
    bad = tambah(tmp_path, 'bayam', 'green')
    (tmp_path / (bad + '.img')).write_bytes(b'rusak')
    result = verifikasi_grup(tmp_path, 'bayam', 'ok', 'HP', 'dev', tmp_path / 'dataset.csv')
    assert result['berhasil'] == [good]
    assert result['gagal'][0]['id'] == bad
    assert [r['id'] for r in daftar_pending(tmp_path)] == [bad]
