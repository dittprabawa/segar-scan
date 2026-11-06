"""Lihat antrean foto dan verifikasi label manusia sebelum evaluasi; mendukung penyimpanan lokal dan online."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Optional

# Pastikan root proyek ada di sys.path agar impor 'backend' dan 'ml' bekerja saat dijalankan langsung
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from backend.app.services.dataset_pending import (
    direktori,
    verifikasi as verifikasi_pending,
    daftar_pending as fetch_pending,
    sync_ke_lokal,
    info_penyimpanan,
)
from scripts.update_dataset import DEFAULT_DATASET


def normalisasi(value):
    return ' '.join(str(value or '').casefold().split())


def daftar_pending(root=None, jenis=None, storage=None):
    """Ambil daftar foto pending dari storage (lokal atau online)."""
    return fetch_pending(root=root, jenis=jenis, storage=storage)


def verifikasi_grup(root, jenis, status, perangkat, split, dataset,
                    label=None, exclude=(), dry_run=False, storage=None):
    if not normalisasi(jenis) or normalisasi(jenis) == 'unknown':
        raise ValueError('Pilih jenis spesifik; unknown harus ditinjau per foto')
    if not normalisasi(label or jenis) or not perangkat.strip():
        raise ValueError('Label dan perangkat wajib diisi')
    if status not in {'ok', 'ditolak'} or split not in {'dev', 'test'}:
        raise ValueError('Status atau split tidak valid')
    # Daftar dibekukan sebelum proses: unggahan baru tidak ikut tanpa tinjauan admin.
    records = daftar_pending(root=root, jenis=jenis, storage=storage)
    ids = {r['id'] for r in records}
    if set(exclude) - ids:
        raise ValueError('ID pengecualian tidak ditemukan dalam kelompok pending ini')
    selected = [r for r in records if r['id'] not in set(exclude)]
    result = {'dipilih': [r['id'] for r in selected], 'berhasil': [], 'gagal': []}
    if dry_run:
        return result
    for record in selected:
        try:
            verifikasi_pending(
                record['id'],
                normalisasi(label or jenis),
                status,
                perangkat,
                split,
                dataset,
                root=root,
                storage=storage,
            )
            result['berhasil'].append(record['id'])
        except (OSError, ValueError, KeyError) as exc:
            result['gagal'].append({'id': record['id'], 'error': str(exc)})
    return result


def opsi_label(parser):
    parser.add_argument('--jenis', required=True)
    parser.add_argument('--status', choices=['ok', 'ditolak'], required=True)
    parser.add_argument('--perangkat', required=True)
    parser.add_argument('--split', choices=['dev', 'test'], default='dev')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending-dir', type=Path, default=direktori(),
                        help='Direktori penyimpanan pending lokal')
    parser.add_argument('--storage', choices=['auto', 'local', 'supabase', 's3', 'r2', 'vercel_blob'],
                        default='auto', help='Target storage backend (default: auto)')

    commands = parser.add_subparsers(dest='command', required=True)

    # list
    listing = commands.add_parser('list', help='Daftar foto yang menunggu verifikasi')
    listing.add_argument('--jenis', help='Filter jenis prediksi, kecocokan persis')

    # groups
    grouping = commands.add_parser('groups', help='Jumlah pending per jenis prediksi')
    grouping.add_argument('--jenis', help='Filter jenis prediksi tertentu')

    # sync
    commands.add_parser('sync', help='Sinkronkan/unduh foto pending dari online storage ke disk lokal')

    # info
    commands.add_parser('info', help='Lihat konfigurasi storage backend yang aktif')

    # verify
    review = commands.add_parser('verify', help='Verifikasi satu foto pending')
    review.add_argument('id')
    opsi_label(review)

    # verify-group
    batch = commands.add_parser('verify-group', help='Verifikasi seluruh pending dari satu jenis prediksi')
    opsi_label(batch)
    batch.add_argument('--label', help='Label manusia jika berbeda dari jenis prediksi')
    batch.add_argument('--exclude', action='append', default=[], metavar='ID',
                       help='Kecualikan ID foto; boleh digunakan beberapa kali')
    batch.add_argument('--dry-run', action='store_true', help='Lihat ID terpilih tanpa menyimpan')

    args = parser.parse_args()
    storage_choice = None if args.storage == 'auto' else args.storage

    # Jika --pending-dir diubah dari default dan --storage auto, prioritaskan lokal untuk direktori tersebut
    root_arg = args.pending_dir if (args.storage == 'local' or args.pending_dir != direktori()) else None

    try:
        if args.command == 'info':
            print(json.dumps(info_penyimpanan(root=root_arg, storage=storage_choice), indent=2))
        elif args.command == 'sync':
            hasil = sync_ke_lokal(root=args.pending_dir, storage=storage_choice)
            print(json.dumps(hasil, indent=2))
        elif args.command == 'list':
            records = daftar_pending(root=root_arg, jenis=args.jenis, storage=storage_choice)
            for record in records:
                print(json.dumps({'id': record['id'], 'foto': record.get('foto'),
                                  'prediksi_sementara': record.get('prediksi_sementara')},
                                 ensure_ascii=False))
            print(f'{len(records)} foto menunggu verifikasi.')
        elif args.command == 'groups':
            records = daftar_pending(root=root_arg, jenis=getattr(args, 'jenis', None), storage=storage_choice)
            groups = Counter(normalisasi(r.get('prediksi_sementara', {}).get('jenis')) or 'unknown'
                             for r in records)
            for jenis, count in sorted(groups.items()):
                print(f'{jenis}: {count} foto')
            print(f'Total: {sum(groups.values())} foto pending.')
        elif args.command == 'verify-group':
            result = verifikasi_grup(root_arg, args.jenis, args.status,
                                     args.perangkat, args.split, args.dataset,
                                     args.label, args.exclude, args.dry_run,
                                     storage=storage_choice)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if args.dry_run:
                print(f"Pratinjau: {len(result['dipilih'])} foto; tidak ada perubahan.")
            else:
                print(f"{len(result['berhasil'])} berhasil, {len(result['gagal'])} gagal.")
                if result['gagal']:
                    raise SystemExit(1)
        else:
            verifikasi_pending(args.id, args.jenis, args.status, args.perangkat,
                               args.split, args.dataset, root=root_arg, storage=storage_choice)
            print(f'Foto diverifikasi dan masuk dataset: {args.dataset}')
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
