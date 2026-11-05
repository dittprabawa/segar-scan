# Evaluasi jenis sayur (BE20)

Dataset belum berisi foto atau label nyata. `dataset.csv` adalah template, bukan hasil evaluasi. Kandidat prompt belum diaktifkan pada aplikasi dan belum terbukti lebih akurat.

1. Kumpulkan minimal 30 foto sayuran yang jenisnya sudah diperiksa manusia, dari beberapa HP dan kondisi cahaya. Sertakan jenis yang sering tertukar.
2. Letakkan foto yang boleh dikirim ke Gemini di `evaluasi/foto/`.
3. Isi `dataset.csv` dengan kolom `file,jenis_benar,split`. Path relatif terhadap lokasi CSV. Contoh format (bukan data yang sudah tersedia):

```csv
file,jenis_benar,split
foto/bayam_01.jpg,bayam,dev
foto/caisim_02.jpg,caisim,test
```

Pisahkan individu sayur/sesi pemotretan antara `dev` dan `test`. Gunakan `dev` untuk memperbaiki kandidat berdasarkan pasangan jenis yang tertukar. Jangan menyetel prompt berdasarkan `test`; gunakan set tersebut untuk validasi akhir. Samakan ejaan label; alat hanya menormalkan kapitalisasi dan spasi, tidak menyamakan sinonim secara otomatis.

Jalankan dari root proyek, dengan environment Gemini yang aktif:

```bash
python -m scripts.evaluasi_jenis --model NAMA_MODEL --split dev --output evaluasi/hasil-dev-01
python -m scripts.evaluasi_jenis --model NAMA_MODEL --split test --output evaluasi/hasil-test-01
```

Ganti `NAMA_MODEL` dengan satu model yang tersedia untuk akun. Skrip mengirim foto ke Gemini dua kali per foto (baseline dan kandidat) dan memakai kuota API. Filter kualitas lokal dilewati supaya perbandingan fokus pada prompt identifikasi. Urutan baseline/kandidat bergantian per foto; model yang digunakan sama.

Output mencakup salinan kedua prompt, metadata model/versi/split, `hasil.csv`, dan `ringkasan.json`. Akurasi menggunakan seluruh foto sebagai penyebut, termasuk penolakan dan error, agar menolak lebih banyak foto tidak terlihat sebagai peningkatan palsu. Error dan penolakan dilaporkan terpisah. Jika kuota/layanan bermasalah, ulangi evaluasi sebelum menyimpulkan perubahan akurasi. Hasil Gemini bisa bervariasi; ulangi perbandingan bila selisih kecil.

BE20 baru dapat dinyatakan selesai setelah hasil test menunjukkan peningkatan yang layak, pasangan salah identifikasi diperiksa, dan prompt yang teruji diterapkan beserta kenaikan `PROMPT_VERSION`. Prompt aktif sekarang p2; aturan identifikasi telah diperjelas setelah laporan bayam diprediksi caisim, tetapi peningkatan akurasinya belum dievaluasi. Perubahan kandidat hanya berlaku dalam proses skrip evaluasi.
