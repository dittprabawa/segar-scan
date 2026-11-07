"""Periksa atau bersihkan foto pending kedaluwarsa; mendukung penyimpanan lokal dan online."""
import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from backend.app.services.dataset_pending import bersihkan, direktori


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending-dir', type=Path, default=direktori(),
                        help='Direktori pending lokal')
    parser.add_argument('--storage', choices=['auto', 'local', 'supabase', 'vercel_blob'],
                        default='auto', help='Target storage backend (default: auto)')
    parser.add_argument('--apply', action='store_true',
                        help='Hapus pending kedaluwarsa; default hanya pratinjau')
    args = parser.parse_args()

    storage_choice = None if args.storage == 'auto' else args.storage
    root_arg = args.pending_dir if (args.storage == 'local' or args.pending_dir != direktori()) else None

    result = bersihkan(root=root_arg, dry_run=not args.apply, storage=storage_choice)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
