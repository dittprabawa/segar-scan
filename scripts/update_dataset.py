"""Tambah foto atau perbarui label dataset evaluasi tanpa memanggil Gemini."""
import argparse
import csv
import fcntl
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.gambar import BukanGambar, TerlaluBesar, buka_gambar

FIELDS = ['filename', 'human_label', 'device_source', 'file', 'jenis_benar', 'split']
DEFAULT_DATASET = Path(__file__).resolve().parent / 'dataset.csv'
MAX_BYTES = 5 * 1024 * 1024


def update_dataset(image, jenis, status, device, split='dev', dataset=DEFAULT_DATASET):
    image, dataset = Path(image), Path(dataset)
    jenis, device = ' '.join(jenis.lower().split()), device.strip()
    if not jenis or not device:
        raise ValueError('Jenis dan sumber perangkat wajib diisi')
    if status not in {'ok', 'ditolak'} or split not in {'dev', 'test'}:
        raise ValueError('Status atau split tidak valid')
    with image.open('rb') as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Foto terlalu besar (maks 5 MB)')
    try:
        buka_gambar(data).close()
    except (BukanGambar, TerlaluBesar) as exc:
        raise ValueError(f'Foto tidak valid: {exc}') from exc
    # Identitas berdasarkan isi: nama sumber berbeda tidak menggandakan foto.
    filename = hashlib.sha256(data).hexdigest() + '.img'
    relative = 'images/' + filename
    dataset.parent.mkdir(parents=True, exist_ok=True)
    with dataset.with_suffix(dataset.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows, fields = [], FIELDS.copy()
        if dataset.exists() and dataset.stat().st_size:
            with dataset.open(newline='', encoding='utf-8') as source:
                reader = csv.DictReader(source)
                if not {'filename', 'human_label', 'device_source'}.issubset(reader.fieldnames or []):
                    raise ValueError('Header dataset tidak kompatibel')
                fields += [f for f in reader.fieldnames if f not in fields]
                rows = list(reader)
                if any(None in row for row in rows):
                    raise ValueError('Baris CSV memiliki kolom berlebih')
        matches = [r for r in rows if r.get('filename') == filename or r.get('file') == relative]
        if len(matches) > 1:
            raise ValueError('Dataset memiliki foto duplikat; rapikan sebelum update')
        row = matches[0] if matches else {}
        row.update(filename=filename, human_label=status, device_source=device,
                   file=relative, jenis_benar=jenis, split=split)
        if not matches:
            rows.append(row)
        target = dataset.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink() or target.is_symlink() or dataset.is_symlink():
            raise ValueError('Symlink tidak boleh digunakan untuk penyimpanan dataset')
        if target.exists() and target.read_bytes() != data:
            raise ValueError('Isi foto tersimpan tidak cocok dengan identitasnya')
        csv_buffer = io.StringIO(newline='')
        writer = csv.DictWriter(csv_buffer, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        payload = csv_buffer.getvalue()
        # Kuota mencakup folder images dan CSV; periksa di dalam lock penulis.
        from backend.app.services.storage_policy import usage, positive, StorageFull
        current = usage(target.parent) + (dataset.stat().st_size if dataset.exists() else 0)
        extra = (0 if target.exists() else len(data)) + len(payload.encode())
        if current + extra > positive('DATASET_VERIFIED_MAX_MB', 2048) * 1024 * 1024:
            raise StorageFull('Batas DATASET_VERIFIED_MAX_MB tercapai')
        new_image = not target.exists()
        if new_image:
            from backend.app.services.dataset_pending import tulis
            tulis(target, data)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=dataset.parent, delete=False) as output:
                temporary = output.name
                output.write(payload)
            os.replace(temporary, dataset)
        except Exception:
            if new_image:
                target.unlink(missing_ok=True)
            raise
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()
    return 'diperbarui' if matches else 'ditambahkan'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--foto', type=Path, required=True)
    parser.add_argument('--jenis', required=True, help='Label jenis dari manusia; mis. bayam atau daging ayam; unknown untuk objek di luar cakupan')
    parser.add_argument('--status', choices=['ok', 'ditolak'], required=True,
                        help='Ekspektasi status penilaian, bukan kategori kesegaran')
    parser.add_argument('--perangkat', required=True)
    parser.add_argument('--split', choices=['dev', 'test'], default='dev')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    try:
        result = update_dataset(args.foto, args.jenis, args.status, args.perangkat,
                                args.split, args.dataset)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Foto {result}: {args.dataset}')


if __name__ == '__main__':
    main()
