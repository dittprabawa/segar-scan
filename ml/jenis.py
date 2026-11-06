"""Baseline nearest-neighbor jenis bahan: histogram warna, tata letak, dan tekstur.

Tidak menilai kesegaran. Jarak bukan probabilitas; model belum terkalibrasi.
"""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

VERSION = 'jenis-knn-v1'
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / 'models' / 'jenis.json'


def fitur(image):
    image = ImageOps.exif_transpose(image).convert('RGB').resize((64, 64))
    arr = np.asarray(image, dtype=np.float32) / 255
    histogram = np.concatenate([np.histogram(arr[:, :, c], bins=16, range=(0, 1))[0] / 4096 for c in range(3)])
    # Rata-rata warna 4x4 blok dan histogram kekuatan tepi.
    spatial = arr.reshape(4, 16, 4, 16, 3).mean(axis=(1, 3)).ravel()
    gray = arr.mean(axis=2)
    edge = np.abs(np.diff(gray, axis=0)).ravel()
    texture = np.histogram(edge, bins=16, range=(0, 1))[0] / edge.size
    return np.concatenate([histogram, spatial, texture])


def baca_data(path):
    path = Path(path)
    with path.open(newline='', encoding='utf-8') as source:
        reader = csv.DictReader(source)
        if not {'file', 'jenis_benar', 'human_label', 'split'}.issubset(reader.fieldnames or []):
            raise ValueError('Dataset wajib memiliki file, jenis_benar, human_label, split')
        rows = list(reader)
    samples, seen = [], set()
    for row in rows:
        if row['human_label'] != 'ok':
            continue
        label = ' '.join(row['jenis_benar'].casefold().split())
        if not label or label == 'unknown':
            raise ValueError('Label jenis manusia kosong/unknown')
        if row['split'] not in {'dev', 'test'}:
            raise ValueError('Split harus dev atau test')
        image_path = (path.parent / row['file']).resolve()
        data = image_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            raise ValueError('Foto duplikat, termasuk lintas split; pisahkan data uji')
        seen.add(digest)
        with Image.open(image_path) as image:
            vector = fitur(image).tolist()
        samples.append({'label': label, 'split': row['split'], 'sha256': digest, 'fitur': vector})
    return samples


class ModelJenis:
    def __init__(self, document):
        if document.get('version') != VERSION:
            raise ValueError('Versi model jenis tidak cocok')
        self.document = document
        self.samples = document['samples']
        self.vectors = np.asarray([s['fitur'] for s in self.samples], dtype=float)
        self.mean = np.asarray(document['mean'])
        self.scale = np.asarray(document['scale'])
        if not self.samples or self.vectors.ndim != 2 or self.vectors.shape[1] != 112:
            raise ValueError('Model jenis kosong/rusak')
        if not np.isfinite(self.vectors).all() or not np.isfinite(self.scale).all() or np.any(self.scale <= 0):
            raise ValueError('Parameter model tidak valid')

    @classmethod
    def load(cls, path=DEFAULT_MODEL):
        return cls(json.loads(Path(path).read_text()))

    def predict_vector(self, vector):
        distances = np.sqrt(np.mean(((self.vectors - vector) / self.scale) ** 2, axis=1))
        idx = int(np.argmin(distances))
        return {'jenis': self.samples[idx]['label'], 'jarak': round(float(distances[idx]), 4),
                'metode': VERSION, 'eksperimental': True,
                'jumlah_latih': len(self.samples)}

    def predict(self, image):
        return self.predict_vector(fitur(image))


def train(dataset):
    samples = baca_data(dataset)
    training = [s for s in samples if s['split'] == 'dev']
    test = [s for s in samples if s['split'] == 'test']
    labels = sorted({s['label'] for s in training})
    if len(labels) < 2:
        raise ValueError('Training memerlukan minimal dua jenis pada split dev')
    vectors = np.asarray([s['fitur'] for s in training])
    document = {'version': VERSION, 'classes': labels, 'mean': vectors.mean(axis=0).tolist(),
                'scale': np.maximum(vectors.std(axis=0), 0.05).tolist(), 'samples': training}
    model = ModelJenis(document)
    predictions = [{'benar': s['label'], 'prediksi': model.predict_vector(s['fitur'])['jenis']}
                   for s in test]
    report = {'jumlah_latih': len(training), 'jumlah_test': len(test),
              'per_jenis': {label: sum(s['label'] == label for s in training) for label in labels},
              'akurasi_test': sum(p['benar'] == p['prediksi'] for p in predictions) / len(test) if test else None,
              'hasil_test': predictions,
              'catatan': 'Baseline eksperimental; dev untuk latihan, test tidak dipakai melatih. Bukan model kesegaran.'}
    document['evaluasi'] = report
    return document, report
