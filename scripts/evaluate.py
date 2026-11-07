import csv
import time
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Muat variabel lingkungan
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Evaluasi")

# Import service backend kamu
from backend.app.services.inference import InferenceService
from backend.app.services.gambar import buka_gambar, TerlaluBesar, BukanGambar

# ==========================================
# KONFIGURASI EVALUASI
# ==========================================
DATASET_CSV = Path("scripts/dataset.csv")
IMAGES_DIR = Path("scripts/images")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1.0.0")

def hitung_akurasi():
    logger.info(f"=== Memulai Evaluasi SegerScan ===")
    logger.info(f"Versi Prompt Gemini : {PROMPT_VERSION}")
    
    service = InferenceService()
    if not getattr(service, "siap", service.model is not None):
        logger.error("Model/Gemini tidak siap. Hentikan evaluasi.")
        return

    total_data = 0
    prediksi_benar = 0
    waktu_total = 0
    
    ukuran_file_stats = []
    
    hasil_detail = []

    with open(DATASET_CSV, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row['filename']
            human_label = row['human_label'].lower()
            device = row['device_source']
            
            filepath = IMAGES_DIR / filename
            if not filepath.exists():
                logger.warning(f"File {filename} tidak ditemukan, lewati.")
                continue
                
            total_data += 1
            file_size_kb = filepath.stat().st_size / 1024
            ukuran_file_stats.append(file_size_kb)

            try:
                with open(filepath, "rb") as img_file:
                    data = img_file.read()
                    
                # Mulai hitung waktu inferensi
                start_time = time.time()
                
                # Proses gambar (Sesuai alur di backend)
                image = buka_gambar(data)
                result = service.predict(image)
                
                durasi = time.time() - start_time
                waktu_total += durasi
                
                # Ekstrak label dari hasil model (sesuaikan dengan format response modelmu)
                # Asumsi result memiliki atribut/dictionary 'status' atau 'label'
                gemini_label = result.status.lower() if hasattr(result, 'status') else result['status'].lower()
                
                is_correct = (gemini_label == human_label)
                if is_correct:
                    prediksi_benar += 1
                
                hasil_detail.append({
                    "file": filename,
                    "hp": device,
                    "ukuran_kb": round(file_size_kb, 2),
                    "human": human_label,
                    "gemini": gemini_label,
                    "benar": is_correct,
                    "durasi": round(durasi, 2)
                })
                
                logger.info(f"[{'BENAR' if is_correct else 'SALAH'}] {filename} | "
                            f"Manusia: {human_label} | Gemini: {gemini_label} | Waktu: {durasi:.2f}s")
                
            except Exception as e:
                logger.error(f"[ERROR] {filename}: {str(e)}")

    # ==========================================
    # LAPORAN HASIL EVALUASI
    # ==========================================
    akurasi = (prediksi_benar / total_data) * 100 if total_data > 0 else 0
    avg_time = waktu_total / total_data if total_data > 0 else 0
    
    min_size = min(ukuran_file_stats) if ukuran_file_stats else 0
    max_size = max(ukuran_file_stats) if ukuran_file_stats else 0
    avg_size = sum(ukuran_file_stats) / len(ukuran_file_stats) if ukuran_file_stats else 0

    print("\n" + "="*40)
    print("LAPORAN EVALUASI AKHIR")
    print("="*40)
    print(f"Versi Prompt       : {PROMPT_VERSION}")
    print(f"Total Foto Uji     : {total_data}")
    print(f"Prediksi Benar     : {prediksi_benar}")
    print(f"Akurasi Keseluruhan: {akurasi:.2f}%")
    print(f"Rata-rata Waktu    : {avg_time:.2f} detik/foto")
    print("-" * 40)
    print("KALIBRASI FILTER FOTO")
    print(f"Ukuran Terkecil    : {min_size:.2f} KB")
    print(f"Ukuran Terbesar    : {max_size:.2f} KB")
    print(f"Rata-rata Ukuran   : {avg_size:.2f} KB")
    print("Tindakan          : Gunakan data ukuran ini untuk merevisi `MAKS_BYTES` di main.py jika perlu.")
    print("="*40)
    
    # Opsional: Simpan laporan ke CSV baru
    simpan_laporan_csv(hasil_detail, PROMPT_VERSION)

def simpan_laporan_csv(hasil, versi_prompt):
    output_file = f"scripts/laporan_evaluasi_{versi_prompt}.csv"
    with open(output_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=["file", "hp", "ukuran_kb", "human", "gemini", "benar", "durasi"])
        writer.writeheader()
        writer.writerows(hasil)
    logger.info(f"Laporan detail disimpan di: {output_file}")

if __name__ == "__main__":
    hitung_akurasi()