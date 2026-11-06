"""Latih ulang baseline jenis dari CSV terverifikasi; tidak membaca pending."""
import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.dataset_pending import tulis
from ml.jenis import DEFAULT_MODEL, train
from scripts.update_dataset import DEFAULT_DATASET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--output', type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    try:
        document, report = train(args.dataset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tulis(args.output, json.dumps(document, ensure_ascii=False).encode())
        report_path = args.output.with_suffix('.report.json')
        tulis(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'Model: {args.output}\nLaporan: {report_path}')


if __name__ == '__main__':
    main()
