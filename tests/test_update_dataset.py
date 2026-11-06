import csv

from PIL import Image
import pytest

from scripts.update_dataset import update_dataset
from scripts.evaluasi_jenis import baca_dataset


def test_tambah_update_dan_baca_evaluasi(tmp_path):
    foto = tmp_path / 'foto.png'
    Image.new('RGB', (20, 20), 'green').save(foto)
    dataset = tmp_path / 'data' / 'dataset.csv'
    assert update_dataset(foto, ' Bayam ', 'ok', 'HP', dataset=dataset) == 'ditambahkan'
    rows = baca_dataset(dataset, 'dev')
    assert rows[0]['jenis_benar'] == 'bayam'
    assert rows[0]['path'].read_bytes() == foto.read_bytes()
    copy = tmp_path / 'nama-lain.png'
    copy.write_bytes(foto.read_bytes())
    assert update_dataset(copy, 'caisim', 'ditolak', 'HP2', 'test', dataset) == 'diperbarui'
    with dataset.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]['human_label'] == 'ditolak'
    assert rows[0]['device_source'] == 'HP2'
    assert baca_dataset(dataset, 'test')[0]['jenis_benar'] == 'caisim'
    with pytest.raises(ValueError, match='Belum ada foto'):
        baca_dataset(dataset, 'dev')


@pytest.mark.parametrize('data', [b'bukan gambar', b'x' * (5 * 1024 * 1024 + 1)])
def test_foto_invalid_tidak_mengubah_dataset(tmp_path, data):
    dataset = tmp_path / 'dataset.csv'
    dataset.write_text('filename,human_label,device_source\nlama.png,ok,HP\n')
    before = dataset.read_bytes()
    foto = tmp_path / 'invalid.png'
    foto.write_bytes(data)
    with pytest.raises(ValueError):
        update_dataset(foto, 'bayam', 'ok', 'HP', dataset=dataset)
    assert dataset.read_bytes() == before


def test_baris_lama_dipertahankan(tmp_path):
    dataset = tmp_path / 'dataset.csv'
    dataset.write_text('filename,human_label,device_source,catatan\nlama.png,ok,HP,simpan\n')
    foto = tmp_path / 'foto.png'
    Image.new('RGB', (20, 20)).save(foto)
    update_dataset(foto, 'bayam', 'ok', 'HP', dataset=dataset)
    with dataset.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]['catatan'] == 'simpan'
