# 🥬 SegerScan

**Estimasi tingkat kesegaran sayuran (0–100%) dari foto kamera, langsung di browser.**

SegerScan adalah aplikasi web yang menerima foto sayuran (dari kamera atau unggahan), lalu model *deep learning* memperkirakan skor kesegarannya dalam persen beserta kategorinya (Sangat segar, Segar, Segera olah, Layu, Tidak layak).

> **Status:** prototipe (MVP). Alur lengkap sudah berjalan: foto → model → API → tampilan web. Model masih perlu dilatih dengan data sungguhan, dan klasifikasi jenis sayuran belum dibuat (`jenis` masih `unknown`).

## Daftar Isi

- [Fitur](#fitur)
- [Cara Kerja](#cara-kerja)
- [Struktur Proyek](#struktur-proyek)
- [Memulai](#memulai)
- [Menyiapkan Data](#menyiapkan-data)
- [Training Model](#training-model)
- [Menjalankan Aplikasi](#menjalankan-aplikasi)
- [Dokumentasi API](#dokumentasi-api)
- [Pengujian](#pengujian)
- [Kategori Kesegaran](#kategori-kesegaran)
- [Keterbatasan](#keterbatasan)
- [Roadmap](#roadmap)
- [Pemecahan Masalah](#pemecahan-masalah)
- [Kontribusi](#kontribusi)
- [Lisensi](#lisensi)

## Fitur

- Ambil foto langsung dari kamera browser (`getUserMedia`) atau unggah gambar
- Skor kesegaran dalam persen (0–100) dan kategori berwarna
- REST API (FastAPI) dengan dokumentasi interaktif otomatis
- Penanganan error yang jelas: model belum tersedia (503), file bukan gambar (400)
- Pembagian data per individu sayuran untuk mencegah kebocoran data
- Pengujian otomatis dengan `pytest`

## Cara Kerja

```
Kamera/Upload (browser) → POST /predict (FastAPI) → EfficientNet-B0 (regresi) → skor 0–100 + kategori → tampil di web
```

- **Model:** EfficientNet-B0 (pretrained ImageNet) dengan satu neuron keluaran untuk regresi skor kesegaran
- **Label:** skor 0–100 per foto, dinormalisasi menjadi 0–1 saat training
- **Loss:** Huber loss (tahan terhadap label yang agak subjektif)
- **Prapemrosesan:** resize 224×224, normalisasi ImageNet. Fungsi yang sama dipakai saat training dan inferensi agar hasilnya konsisten

## Struktur Proyek

```
segarscan/
├── .vscode/               # setting, launch, ekstensi yang direkomendasikan
├── backend/
│   ├── app/
│   │   ├── main.py        # aplikasi FastAPI, endpoint, penyajian frontend
│   │   ├── schemas.py     # skema respons
│   │   └── services/
│   │       └── inference.py   # pemuatan model dan pelabelan kategori
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── camera.js      # akses kamera
│       └── app.js         # kirim gambar ke API, tampilkan hasil
├── ml/
│   ├── dataset.py         # Dataset PyTorch dan transformasi gambar
│   ├── model.py           # arsitektur model
│   ├── split.py           # pembagi train/val/test per individu
│   ├── train.py           # training loop + simpan model terbaik
│   ├── evaluate.py        # fungsi metrik: MAE, RMSE, Spearman
│   └── predict.py         # inferensi satu gambar
├── data/                  # tidak di-commit (kecuali label dan split)
│   ├── raw/<jenis>/       # foto asli per jenis sayuran
│   ├── processed/
│   ├── labels/skor_kesegaran.csv
│   └── splits/            # train.csv, val.csv, test.csv
├── models/                # bobot hasil training (.pt), tidak di-commit
├── notebooks/             # eksplorasi data dan eksperimen
├── tests/
├── docs/
├── requirements.txt
├── pytest.ini
└── .env.example
```

## Memulai

### Prasyarat

- Python 3.9 atau lebih baru (disarankan 3.10+)
- `git`
- Koneksi internet saat pertama kali training (bobot pretrained diunduh otomatis)

### Instalasi

```bash
git clone <url-repo-anda>
cd segarscan

# 1. Buat dan aktifkan virtual environment
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

# 2. Pasang dependensi
python -m pip install -r requirements.txt
python -m pip install -r backend/requirements.txt
python -m pip install pytest httpx   # untuk pengujian
```

Gunakan `python -m pip` agar paket pasti terpasang ke virtual environment yang aktif.

### Konfigurasi (opsional)

Nilai bawaan sudah cukup untuk pengembangan lokal. Variabel yang dikenali:

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `MODEL_PATH` | `models/segarscan.pt` | Lokasi bobot model |

Contoh: `export MODEL_PATH=models/model_baru.pt`. File `.env.example` hanya contoh isi.

## Menyiapkan Data

### Penempatan foto

Simpan foto per jenis sayuran:

```
data/raw/bayam/bayam_b01_hari0_hpA.jpg
data/raw/tomat/tomat_t01_hari0_hpA.jpg
```

Format nama yang disarankan: `jenis_idindividu_hariN_hp.jpg`.

### File label

Semua label ada di satu file, `data/labels/skor_kesegaran.csv`:

```csv
nama_file,jenis,id_individu,hari_ke,skor,penilai
bayam_b01_hari0_hpA.jpg,bayam,b01,0,95,rata-rata 3 penilai
bayam_b01_hari2_hpA.jpg,bayam,b01,2,78,rata-rata 3 penilai
```

| Kolom | Keterangan |
|---|---|
| `nama_file` | Sama persis dengan nama file di `data/raw/<jenis>/` |
| `jenis` | Nama sub-folder jenis sayuran |
| `id_individu` | ID sayuran fisik yang difoto berulang (dipakai untuk pembagian data) |
| `hari_ke` | Hari pengamatan sejak foto pertama |
| `skor` | Skor kesegaran **0–100** |
| `penilai` | Catatan penilai atau sumber skor |

Panduan pelabelan:

- Susun rubrik tertulis yang sama untuk semua penilai (warna, kelayuan, bercak/lendir, kilap)
- Gunakan minimal 3 penilai per foto, lalu rata-ratakan skornya
- Variasikan pencahayaan, latar, sudut, dan jenis ponsel
- Foto berseri harian dari sayuran yang sama menghasilkan data yang paling objektif

### Membagi data

```bash
python -m ml.split
```

Pembagian dilakukan **per individu sayuran** (70% train, 15% val, 15% test), sehingga foto dari sayuran yang sama tidak muncul di dua set sekaligus. Hasilnya ada di `data/splits/`. Skrip ini membutuhkan minimal 5 individu.

Untuk **uji alur dengan data percobaan yang sangat kecil saja**, gunakan pembagian acak:

```bash
python -m ml.split --acak
```

Jangan memakai `--acak` untuk data sungguhan karena hasil evaluasinya akan terlihat lebih bagus daripada kenyataan.

## Training Model

```bash
python -m ml.train --epochs 20 --batch_size 32
```

| Argumen | Bawaan | Keterangan |
|---|---|---|
| `--train_csv` | `data/splits/train.csv` | Data latih |
| `--val_csv` | `data/splits/val.csv` | Data validasi |
| `--img_root` | `data/raw` | Folder induk foto |
| `--out` | `models/segarscan.pt` | Lokasi simpan model |
| `--epochs` | `20` | Jumlah epoch |
| `--batch_size` | `32` | Ukuran batch |
| `--lr` | `1e-4` | Learning rate |

Model dengan loss validasi terendah disimpan otomatis. Jika laptop lambat, training bisa dilakukan di Google Colab atau Kaggle, lalu berkas `.pt` disalin ke folder `models/`.

### Evaluasi

`ml/evaluate.py` menyediakan fungsi `evaluate(y_true, y_pred)` yang menghitung:

- **MAE** dan **RMSE**: rata-rata dan akar kuadrat selisih skor
- **Spearman**: apakah urutan kesegaran sudah benar

Hitung metrik pada **test set** yang tidak dipakai untuk tuning, dan periksa juga per jenis sayuran serta dengan foto dari kamera ponsel yang sebenarnya.

## Menjalankan Aplikasi

```bash
uvicorn backend.app.main:app --reload
```

Jalankan dari folder utama proyek. Lalu buka:

| Alamat | Fungsi |
|---|---|
| `http://localhost:8000/ui/` | Halaman kamera (tanda `/` di akhir wajib ada) |
| `http://localhost:8000/docs` | Dokumentasi dan uji API interaktif |
| `http://localhost:8000/` | Cek status server |

**Kamera di ponsel atau jaringan lain** membutuhkan **HTTPS**. Di `localhost` kamera berjalan tanpa HTTPS. Untuk uji dari ponsel, gunakan layanan terowongan HTTPS atau deploy ke hosting yang menyediakan HTTPS. Selama kamera tidak tersedia, tombol Upload tetap bisa dipakai.

## Dokumentasi API

### `POST /predict`

Mengirim satu gambar, menerima estimasi kesegaran.

**Permintaan** (`multipart/form-data`): field `file` berisi gambar.

```bash
curl -X POST http://localhost:8000/predict -F "file=@foto_bayam.jpg"
```

**Respons 200**

```json
{
  "skor": 72.0,
  "label": "Segar",
  "jenis": "unknown"
}
```

| Kode | Arti |
|---|---|
| `200` | Berhasil |
| `400` | File bukan gambar yang valid |
| `422` | Field `file` tidak dikirim |
| `503` | Model belum tersedia (`models/segarscan.pt` belum ada) |

## Pengujian

```bash
python -m pytest -v
```

Jalankan dari folder utama. File `pytest.ini` sudah mengatur `pythonpath = .` agar paket `backend` dan `ml` terbaca. Pengujian API memakai model tiruan, jadi dapat berjalan walau model asli belum dilatih.

## Kategori Kesegaran

| Skor | Kategori |
|---|---|
| 80–100 | Sangat segar |
| 60–79 | Segar |
| 40–59 | Segera olah |
| 20–39 | Layu |
| 0–19 | Tidak layak |

## Keterbatasan

- Hasil adalah **estimasi visual** dan bukan jaminan keamanan pangan
- Akurasi sangat bergantung pada kualitas dan keragaman data (cahaya, latar, jenis ponsel)
- Penilaian kesegaran bersifat subjektif, sehingga konsistensi rubrik menentukan kualitas model
- Klasifikasi jenis sayuran belum tersedia, sehingga model dilatih dan diuji per kondisi data yang ada
- Model percobaan dari data sangat kecil hanya untuk menguji alur, bukan untuk demo

## Roadmap

- [x] Kerangka proyek, API, dan frontend kamera
- [x] Pengujian otomatis API
- [x] Skrip pembagi data per individu
- [ ] Pengumpulan data sungguhan dan rubrik penilaian
- [ ] Skrip evaluasi test set dari baris perintah
- [ ] Model klasifikasi jenis sayuran
- [ ] Penolakan prediksi untuk gambar gelap, buram, atau bukan sayuran
- [ ] Rentang ketidakpastian dan heatmap Grad-CAM
- [ ] Riwayat pemindaian dan dashboard
- [ ] Deploy dengan HTTPS

## Pemecahan Masalah

| Masalah | Penyebab dan solusi |
|---|---|
| `No module named 'pytest'` / `'fastapi'` | Virtual environment belum aktif atau dependensi belum dipasang. Aktifkan `.venv` lalu `python -m pip install -r ...` |
| `ModuleNotFoundError: backend` saat tes | Jalankan `python -m pytest` dari folder utama, bukan dari `tests/` |
| `/predict` membalas 503 | `models/segarscan.pt` belum ada. Jalankan training terlebih dahulu |
| `/ui/` menampilkan Not Found | Gunakan tanda `/` di akhir alamat dan jalankan dari folder utama |
| Kamera tidak menyala | Izin kamera ditolak, atau akses lewat HTTP selain `localhost`. Gunakan tombol Upload |
| `TypeError` pada `str \| None` | Sintaks itu butuh Python 3.10+. Gunakan `Optional[str]` pada Python 3.9 |
| Skor selalu 0 | Model belum terlatih cukup. Periksa jumlah data dan epoch |
| Error `Expected more than 1 value per channel` | Ada batch berisi satu gambar. Sesuaikan `--batch_size` dengan jumlah data |

## Kontribusi

- Cabang: `main` (stabil), `dev`, dan cabang per fitur seperti `feat/camera-ui`
- Jangan meng-*commit* foto dataset atau bobot model. Cukup label (`data/labels/`) dan split (`data/splits/`)
- Jalankan `python -m pytest` sebelum membuat *pull request*
- Gunakan `skor_kesegaran.csv` sebagai satu-satunya sumber label

**Tim:** 
Aselelo