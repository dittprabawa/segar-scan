"""Bandingkan prompt aktif dan kandidat pada foto berlabel, dengan model yang sama.

Jalankan: python -m scripts.evaluasi_jenis --help
"""
import argparse
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv


def normalisasi(teks):
    return " ".join(teks.strip().casefold().split())


def ringkas(rows):
    hasil = {}
    for versi in ("baseline", "kandidat"):
        benar = ditolak = error = 0
        salah = Counter()
        for row in rows:
            status = row[versi + "_status"]
            pred = normalisasi(row[versi + "_jenis"])
            target = normalisasi(row["jenis_benar"])
            if status == "ok" and pred == target:
                benar += 1
            elif status == "ok":
                salah[(target, pred)] += 1
            ditolak += status == "ditolak"
            error += status == "error"
        n = len(rows)
        hasil[versi] = {
            "jumlah_foto": n, "benar": benar,
            "ditolak": ditolak, "error": error,
            "akurasi_semua_foto": benar / n if n else None,
            "jenis_tertukar": [
                {"benar": a, "prediksi": b, "jumlah": count}
                for (a, b), count in sorted(salah.items())
            ],
        }
    hasil["selisih_akurasi"] = (
        hasil["kandidat"]["akurasi_semua_foto"] - hasil["baseline"]["akurasi_semua_foto"]
        if rows else None
    )
    return hasil


def baca_dataset(path, split):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not {"file", "jenis_benar", "split"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV wajib memiliki file,jenis_benar,split")
        rows = [r for r in reader if r["split"] == split]
    if not rows:
        raise ValueError("Belum ada foto berlabel pada split yang dipilih")
    seen = set()
    for r in rows:
        foto = (path.parent / r["file"]).resolve()
        if not r["file"].strip() or not r["jenis_benar"].strip() or not foto.is_file():
            raise ValueError("Setiap baris wajib memiliki foto yang ada dan label manusia")
        if foto in seen:
            raise ValueError("Foto duplikat dalam split evaluasi")
        seen.add(foto)
        r["path"] = foto
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent / "dataset.csv")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--model", required=True, help="Satu model Gemini yang tersedia")
    parser.add_argument("--output", type=Path, required=True, help="Folder baru untuk hasil")
    parser.add_argument("--kandidat", type=Path, default=Path("evaluasi/prompt_kandidat.txt"))
    args = parser.parse_args()
    load_dotenv()
    from backend.app.services import vlm
    from backend.app.services.gambar import buka_gambar
    dataset = baca_dataset(args.dataset, args.split)
    if not vlm.tersedia():
        parser.error("Isi GEMINI_API_KEY dan aktifkan USE_VLM=1")
    if "," in args.model:
        parser.error("Pilih tepat satu model agar perbandingan konsisten")
    os.environ["GEMINI_MODEL"] = args.model
    baseline = vlm.PROMPT
    kandidat = baseline + "\n" + args.kandidat.read_text(encoding="utf-8")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "baseline.txt").write_text(baseline, encoding="utf-8")
    (args.output / "kandidat.txt").write_text(kandidat, encoding="utf-8")
    metadata = {"model": args.model, "baseline_versi": vlm.PROMPT_VERSION,
                "kandidat_versi": vlm.PROMPT_VERSION + "-kandidat", "split": args.split,
                "catatan": "Evaluasi jenis langsung melalui vlm.nilai, tanpa filter QC lokal"}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    rows = []
    fields = ["file", "jenis_benar"] + [v + "_" + k for v in ("baseline", "kandidat")
                                             for k in ("status", "jenis", "alasan", "durasi_ms")]
    try:
        with (args.output / "hasil.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for i, item in enumerate(dataset):
                image = buka_gambar(item["path"].read_bytes())
                row = {"file": item["file"], "jenis_benar": item["jenis_benar"]}
                urutan = [("baseline", baseline), ("kandidat", kandidat)]
                if i % 2:
                    urutan.reverse()
                for versi, prompt in urutan:
                    vlm.PROMPT = prompt
                    mulai = time.monotonic()
                    try:
                        d = vlm.nilai(image, deadline=mulai + 25)
                        status = "error" if d.get("sibuk") else "ok" if d["valid"] else "ditolak"
                        row.update({versi + "_status": status, versi + "_jenis": d.get("jenis", ""),
                                    versi + "_alasan": d.get("alasan", "")})
                    except Exception as exc:
                        row.update({versi + "_status": "error", versi + "_jenis": "",
                                    versi + "_alasan": type(exc).__name__})
                    row[versi + "_durasi_ms"] = round((time.monotonic() - mulai) * 1000)
                writer.writerow(row)
                f.flush()
                rows.append(row)
    finally:
        vlm.PROMPT = baseline
    laporan = ringkas(rows)
    (args.output / "ringkasan.json").write_text(json.dumps(laporan, indent=2), encoding="utf-8")
    print(json.dumps(laporan, indent=2))


if __name__ == "__main__":
    main()
