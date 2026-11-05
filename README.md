# SegerScan

SegerScan memperkirakan kesegaran sayuran dari foto menggunakan Gemini. Pengguna mengambil foto lewat kamera atau mengunggah gambar, lalu menerima jenis sayur, skor 0–100, rentang skor, kategori, tingkat keyakinan, dan saran pembelian.

Aplikasi sudah di-deploy ke Vercel dengan HTTPS. Backend menggunakan FastAPI dan menyajikan frontend dari server yang sama. Penilai aktif hanya Gemini; training dan fallback model lokal tidak digunakan lagi.

Hasil merupakan perkiraan visual, bukan jaminan keamanan pangan. Foto yang lolos pemeriksaan awal dikirim ke layanan Gemini untuk dinilai.

## Alur aplikasi

```text
Kamera / unggah foto
  → POST /predict
  → pemeriksaan ukuran, format, orientasi EXIF, dan jumlah piksel
  → pemeriksaan gelap, terlalu terang/silau, dan buram
  → Gemini: beberapa model dipanggil bersamaan dengan pembatasan slot
  → hasil pertama yang berhasil dipakai
  → backend menyusun kategori dan saran
  → frontend menampilkan hasil atau alasan penolakan
```

Jika penilai gagal, backend mengembalikan 503. Saat semua model sibuk atau terkena pembatasan kuota, sebagian alur mengembalikan status `ditolak` dengan pesan untuk mencoba lagi. Tidak ada fallback ke model lokal.

## Menjalankan secara lokal

Buat virtual environment dengan Python yang kompatibel dengan dependensi di `requirements.txt`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install pytest
```

Di Windows, aktifkan environment dengan `.venv\Scripts\activate`.

Buat `.env` di folder utama proyek:

```dotenv
GEMINI_API_KEY=isi_kunci_sendiri
USE_VLM=1
# Isi nama model yang tersedia untuk akunmu, pisahkan dengan koma.
# GEMINI_MODEL=nama_model_pertama,nama_model_kedua
```

Periksa akses API dan daftar model:

```bash
python cek_gemini.py
```

Perintah tersebut menghubungi API Gemini. Pilih model yang mendukung input gambar, lalu atur `GEMINI_MODEL` bila diperlukan. Nama model bawaan di kode tidak menjamin model tersebut tersedia untuk akunmu.

Jalankan aplikasi dari folder utama:

```bash
uvicorn backend.app.main:app --reload
```

| Alamat lokal | Fungsi |
|---|---|
| http://127.0.0.1:8000/ui/ | Halaman kamera dan unggah foto |
| http://127.0.0.1:8000/docs | Dokumentasi API interaktif |
| http://127.0.0.1:8000/health | Kesiapan penilai |
| http://127.0.0.1:8000/ | Otomatis mengarah ke `/ui/` |

Restart server setelah mengubah `.env`. Opsi `--reload` hanya untuk pengembangan. Akses kamera dari HP memerlukan HTTPS dan izin kamera.

## Konfigurasi

Konfigurasi lokal dibaca dari `.env`; deployment menggunakan environment variables proyek Vercel. Jangan memasukkan kunci API ke frontend atau Git.

| Variabel | Bawaan di kode | Fungsi |
|---|---|---|
| `GEMINI_API_KEY` | Kosong | Kunci akses Gemini |
| `USE_VLM` | `1` | `1` mengaktifkan Gemini; dinonaktifkan berarti tidak ada penilai |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite,gemini-3.8-flash` | Daftar model yang dipanggil bersamaan |
| `TOTAL_TIMEOUT` | `25` | Anggaran waktu inferensi, dalam detik |
| `VLM_TIMEOUT` | `20` | Timeout panggilan per model, dalam detik |
| `MAX_GEMINI_BERSAMAAN` | `3` | Batas permintaan yang memasuki alur Gemini per proses |
| `GATE_TUNGGU` | `2` | Waktu menunggu slot permintaan, dalam detik |
| `VLM_MAX_BERSAMAAN` | `3` | Batas panggilan model Gemini bersamaan per proses |
| `VLM_TUNGGU_SLOT` | `5` | Waktu menunggu slot panggilan model, dalam detik |
| `VLM_COOLDOWN_429` | `30` | Jeda model setelah terkena 429, dalam detik |
| `VLM_UKURAN_GAMBAR` | `768` | Batas sisi terpanjang gambar yang dikirim ke Gemini |
| `VLM_KUALITAS_JPEG` | `82` | Kualitas JPEG yang dikirim ke Gemini |
| `VLM_MAX_TOKEN_JAWABAN` | `1024` | Batas token jawaban |
| `MAX_PIXELS` | `25000000` | Batas jumlah piksel gambar |
| `RATE_LIMIT` | `10/minute` | Batas permintaan prediksi per IP |
| `QC_GELAP` | `35` | Batas bawah rata-rata kecerahan |
| `QC_SILAU` | `240` | Batas atas rata-rata kecerahan |
| `QC_AREA_TERANG` | `0.65` | Proporsi piksel berkecerahan ≥240; diperiksa saat rata-rata ≥220 |
| `QC_BURAM` | `15` | Ambang variansi Laplacian untuk ketajaman |

Batas file di aplikasi adalah 5 MB. `TOTAL_TIMEOUT` belum merupakan jaminan keras waktu seluruh request: upload, pemrosesan gambar, dan waktu tunggu tambahan juga memerlukan waktu. Target maksimal 30 detik masih perlu diuji dan diperketat.

## API

### `POST /predict`

Kirim `multipart/form-data` dengan field `file`:

```bash
curl -X POST http://127.0.0.1:8000/predict -F 'file=@foto_sayur.jpg'
```

Contoh respons berhasil:

```json
{
  "status": "ok",
  "skor": 85.0,
  "skor_min": 80.0,
  "skor_maks": 90.0,
  "label": "Sangat segar",
  "jenis": "bayam",
  "keyakinan": "tinggi",
  "saran": "Layak dibeli",
  "ciri": "Daun tampak hijau dan tegak.",
  "alasan": null,
  "sumber": "gemini"
}
```

Foto yang tidak layak dinilai mendapat HTTP 200 dengan `status: "ditolak"`, skor `null`, dan pesan pada `alasan`. Frontend harus memeriksa `status`, bukan hanya kode HTTP.

| HTTP | Arti |
|---|---|
| 200 | Hasil prediksi atau penolakan penilaian |
| 400 | File bukan gambar atau gambar rusak |
| 413 | File terlalu besar atau jumlah piksel melebihi batas |
| 422 | Field unggahan tidak sesuai, misalnya `file` tidak dikirim |
| 429 | Rate limit terlampaui; disertai `Retry-After: 60` |
| 503 | Penilai tidak tersedia atau gagal; saat ini belum disertai `Retry-After` |

Error yang ditangani aplikasi menggunakan bentuk `{"detail": "pesan"}`. Error tak terduga atau error dari platform hosting belum tentu mengikuti bentuk tersebut.

### `GET /health`

Contoh ketika Gemini diaktifkan dan kunci terisi:

```json
{
  "status": "ok",
  "gemini": true,
  "model_lokal": false,
  "siap": true
}
```

`gemini: true` hanya menunjukkan konfigurasi kunci terisi dan Gemini diaktifkan; endpoint ini tidak menguji validitas kunci, kuota, atau koneksi. Field `model_lokal` masih dipertahankan untuk kompatibilitas dan bernilai `false`.

## Deployment dan operasi Vercel

Repository menggunakan `index.py` sebagai entrypoint yang mengekspor aplikasi dari `backend/app/main.py`. Konfigurasi routing ada di `vercel.json`, dan dependensi runtime ada di `requirements.txt`.

Untuk memperbarui deployment:

1. Jalankan tes lokal sebelum mengirim perubahan.
2. Atur environment variables untuk lingkungan deployment yang dituju, terutama `GEMINI_API_KEY`, `USE_VLM`, dan `GEMINI_MODEL`.
3. Deploy revisi terbaru melalui alur deployment proyek Vercel. Buat deployment baru setelah perubahan environment variables agar konfigurasi diterapkan.
4. Buka `/health` dan `/ui/` pada domain deployment.
5. Uji satu foto normal dan satu foto yang seharusnya ditolak, lalu periksa log runtime deployment tersebut jika hasilnya bermasalah.

Gunakan log runtime untuk melihat kegagalan Gemini, nama model, timeout, atau pesan antrean penuh. Log saat ini belum menyediakan ID dan durasi lengkap untuk setiap permintaan.

Status operasional yang masih perlu diverifikasi:

- IP asli pengguna di belakang proxy Vercel untuk rate limit.
- Timeout dan tampilan pesan error pada deployment nyata.
- Pengujian beberapa permintaan bersamaan dan foto dari HP.

Rate limit, semaphore, dan jeda model disimpan dalam memori tiap proses, bukan pembatas global lintas instance Vercel. CORS saat ini masih terbuka untuk semua asal.

## Pengujian

```bash
python -m pytest -q
```

Tes saat ini tidak memanggil Gemini sungguhan. Cakupannya meliputi foto gelap, buram, terlalu terang, objek dengan latar putih, orientasi EXIF, batas piksel, file bukan gambar, perhitungan sisa waktu, serta antrean Gemini penuh dan pemulihan slot.

Tes endpoint lengkap untuk kode HTTP, rate limit, dan kegagalan Gemini masih perlu ditambahkan. Tes yang lolos belum membuktikan akurasi prediksi, target kecepatan, atau perilaku deployment.

## Penanganan masalah

| Gejala | Pemeriksaan dan tindakan |
|---|---|
| `/health` menunjukkan `gemini: false` | Periksa `GEMINI_API_KEY` dan `USE_VLM=1`; restart lokal atau buat deployment baru |
| `/health` siap tetapi prediksi gagal | Periksa log runtime; kesiapan konfigurasi tidak membuktikan kunci valid atau kuota tersedia |
| Model Gemini tidak ditemukan | Jalankan `python cek_gemini.py` dengan konfigurasi yang sesuai, perbarui `GEMINI_MODEL`, lalu restart/deploy ulang |
| Pesan penilai ramai atau kuota habis | Tunggu jeda model, periksa kuota dan log; bila tersedia, gunakan model lain yang dapat diakses akun |
| HTTP 429 dari aplikasi | Tunggu sesuai `Retry-After`; jika pengguna berbeda ikut dibatasi, periksa pembacaan IP di belakang proxy |
| HTTP 503 | Periksa kunci, akses model, koneksi, timeout, dan kapasitas slot; tidak ada model lokal sebagai cadangan |
| Permintaan lama atau gagal di Vercel | Periksa waktu dan error di log deployment serta batas runtime deployment; cocokkan dengan konfigurasi timeout aplikasi |
| Foto ditolak karena terlalu terang | Kurangi pencahayaan/pantulan dan foto ulang agar warna serta tekstur terlihat |
| Kamera tidak terbuka di HP | Pastikan HTTPS, izin kamera aktif, atau gunakan unggah foto |
| Perubahan konfigurasi belum terlihat | Restart lokal atau buat deployment baru pada lingkungan Vercel yang tepat |

## Struktur kode aktif

```text
backend/app/
  main.py                 # Endpoint, rate limit, CORS, frontend statis
  schemas.py              # Kontrak respons
  services/
    gambar.py             # Decode gambar, EXIF, batas piksel
    quality.py            # Pemeriksaan kualitas lokal tanpa model ML
    vlm.py                # Prompt dan panggilan Gemini
    inference.py          # Alur penilaian, kategori, dan saran
frontend/                 # Halaman, kamera, dan pengiriman foto
tests/                   # Tes otomatis
index.py                  # Entry point Vercel
vercel.json               # Konfigurasi deployment
requirements.txt          # Dependensi runtime
cek_gemini.py             # Pemeriksaan akses dan daftar model
```

Folder `ml/`, bobot dalam `models/`, dan berkas terkait training yang masih tersisa merupakan peninggalan eksperimen; tidak digunakan oleh alur prediksi aktif.

## Pekerjaan lanjutan

- Batasi CORS ke frontend yang diizinkan dan verifikasi IP asli di Vercel.
- Perketat deadline total, lengkapi error, dan tambah pengujian API/beban.
- Tambahkan log berstruktur, versi aplikasi di `/health`, dan versi prompt.
- Evaluasi Gemini terhadap penilaian manusia melalui CSV dan metrik akurasi.
- Kalibrasi ambang kualitas dengan kumpulan foto dari beberapa HP.
- Audit penanganan kunci dan privasi, termasuk perilaku unggahan sementara pada framework/hosting.

Aplikasi tidak secara eksplisit menyimpan foto ke dataset. Namun unggahan diproses oleh framework/hosting dan gambar dikirim ke Gemini; jangan menyatakan bahwa seluruh alur tidak pernah menggunakan penyimpanan sementara.
