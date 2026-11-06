import csv
import json

from PIL import Image
import pytest

from ml.jenis import ModelJenis, train
from scripts.update_dataset import update_dataset
from backend.app.services import jenis_lokal


def dataset(tmp_path):
    csv_path = tmp_path / 'dataset.csv'
    for name, color, split, label in [('red', (255, 0, 0), 'dev', 'tomat'),
                                     ('green', (0, 255, 0), 'dev', 'bayam'),
                                     ('redtest', (240, 5, 0), 'test', 'tomat')]:
        image = tmp_path / (name + '.png')
        Image.new('RGB', (32, 32), color).save(image)
        update_dataset(image, label, 'ok', 'test', split, csv_path)
    return csv_path


def test_train_test_terpisah_dan_reload(tmp_path, monkeypatch):
    document, report = train(dataset(tmp_path))
    assert report['jumlah_latih'] == 2
    assert report['jumlah_test'] == 1
    assert report['akurasi_test'] == 1
    assert {s['split'] for s in document['samples']} == {'dev'}
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('LOCAL_JENIS_MODEL', str(path))
    with Image.new('RGB', (32, 32), 'red') as image:
        assert jenis_lokal.predict(image)['jenis'] == 'tomat'
        document['samples'][0]['label'] = 'label revisi'
        path.write_text(json.dumps(document))
        assert jenis_lokal.predict(image)['jenis'] == 'label revisi'
        monkeypatch.setenv('USE_LOCAL_JENIS', '0')
        assert jenis_lokal.predict(image) is None


def test_tanpa_test_tidak_mengarang_akurasi(tmp_path):
    path = dataset(tmp_path)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(r for r in rows if r['split'] == 'dev')
    _, report = train(path)
    assert report['akurasi_test'] is None


def test_duplikat_lintas_split_ditolak(tmp_path):
    path = dataset(tmp_path)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    with path.open('a') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writerow(dict(rows[0], split='test'))
    with pytest.raises(ValueError, match='duplikat'):
        train(path)


def test_data_ditolak_tidak_dilatih(tmp_path):
    path = dataset(tmp_path)
    image = tmp_path / 'bad.png'
    Image.new('RGB', (30, 30), 'black').save(image)
    update_dataset(image, 'unknown', 'ditolak', 'test', 'dev', path)
    _, report = train(path)
    assert report['jumlah_latih'] == 2
