# Pemeriksaan keamanan SegarScan

Tanggal: 6 November 2026. Lingkup: kode lokal backend, unggahan HTTP, penyimpanan dataset, pencatatan error, akses file statis, dan dependensi virtual environment. Ini pemeriksaan kode dan pengujian terarah, bukan pentest deployment atau sertifikasi keamanan.

## Hasil

- Seluruh suite: **85 tes lolos** dengan `python -m pytest -q -W error`.
- `pip-audit 2.10.1`: **96 paket diperiksa, 0 paket dilewati, 0 kerentanan dikenal ditemukan**, berdasarkan advisori saat pemeriksaan. Ini tidak membuktikan tidak ada kerentanan yang belum diketahui. Paket baru/perubahan versi perlu diperiksa ulang.
- Pratinjau data pending lokal: 1.209.860 byte; tidak ada pending kedaluwarsa. Pemeriksaan ini tidak menghapus foto pengguna.
- Tidak ditemukan `.env` terlacak Git pada checkout saat pemeriksaan. Pemeriksaan ini tidak mencakup seluruh riwayat Git.

## Temuan yang diperbaiki

| Temuan | Perbaikan dan verifikasi |
|---|---|
| Body tanpa Content-Length bisa mencapai parser multipart sebelum batas ukuran ditegakkan | Middleware ASGI membatasi body sebelum parsing, termasuk chunked atau Content-Length lebih kecil dari isi. Batas body 5 MB + 64 KiB overhead, batas isi foto tetap 5 MB. Tes memastikan parser tidak dijalankan ketika body melampaui batas. |
| Foto dapat memenuhi disk tanpa batas aplikasi | Kuota default pending 512 MB dan verified 2048 MB; diperiksa dalam lock. Penambahan ditolak bila penuh. Tes penulisan bersamaan dan rollback saat kuota gagal. |
| Pending disimpan tanpa masa simpan | Pending berumur lebih dari 30 hari dibersihkan saat server hidup (awal, per jam) dan sebelum penyimpanan baru. Metadata/foto verified tidak dihapus otomatis. Tes membuktikan dry-run tidak menulis dan verified tetap ada. |
| Pesan validasi/error berpotensi memuat data unggahan atau respons provider | Log validasi berisi jumlah field; error provider/inferensi memakai nama kelas error, tanpa potongan respons provider atau pesan mentah. Tes memastikan input validasi tidak bocor ke log. |
| Target penyimpanan symlink dapat mengarah keluar lokasi foto | Penolakan symlink pada pasangan foto/metadata pending dan target foto/CSV verified. Tes memastikan target luar tetap utuh. Konfigurasi root direktori tetap harus dikelola operator tepercaya. |
| Klaim wildcard proxy terlalu luas dalam komentar konfigurasi | Komentar diperjelas: wildcard hanya bila jaringan membatasi akses backend ke proxy tepercaya. Default tetap 127.0.0.1. |

## Kontrol yang diperiksa

- Path gambar dari unggahan dibuat dengan hash konten, bukan nama file pengguna.
- ID verifikasi divalidasi sebagai 64 digit heksadesimal; traversal ditolak.
- `.env`, pending, dan CSV tidak tersedia melalui route publik/static; pengujian mencakup traversal URL frontend.
- Origin CORS yang tidak diizinkan tidak menerima header izin. CORS bukan autentikasi dan tidak membatasi klien non-browser.
- Respons penilai ditampilkan melalui `textContent`, bukan HTML dinamis dari provider pada alur hasil frontend yang diperiksa.
- Model jenis aktif disimpan sebagai JSON, bukan pickle/PyTorch yang dimuat dari unggahan. Model PyTorch lama tidak digunakan oleh alur aktif.
- Foto tetap diperiksa oleh decoder, batas byte, dan batas jumlah piksel.
- Handler error 500 tidak membocorkan pesan exception ke respons/log middleware; rate limit dan ID request tetap diuji oleh suite API.

## Batasan dan pekerjaan sebelum produksi publik

1. **Endpoint scan belum memakai autentikasi.** Siapa pun yang dapat mengakses server dapat memakai kuota Gemini. Rate limit saat ini per IP/per proses; belum membatasi total biaya atau seluruh instance. Tambahkan autentikasi dan pembatas global bila deployment membutuhkan akses terbatas.
2. **Batas body bukan pertahanan DoS lengkap.** Buffer maksimal tetap memakai memori per request. Terapkan batas body, koneksi, waktu baca, dan konkurensi pada reverse proxy/hosting; pengujian beban dan slow-upload belum dilakukan.
3. **Penyimpanan masih lokal.** Batas pending dan verified terpisah, bukan kuota filesystem. Log, model, laporan, temporary upload, dan direktori alternatif tidak dihitung. Tidak ada backup, enkripsi aplikasi, atau cloud storage; kapasitas dan izin OS tetap tanggung jawab operator.
4. **Verified tidak kedaluwarsa otomatis.** Data ini dapat memenuhi kuota lalu menghentikan penambahan baru. Ekspor/arsip manual diperlukan. Metadata rusak dan file tak dikenal dipertahankan, bukan dihapus secara spekulatif.
5. **Proxy harus dikonfigurasi benar.** Jangan mempercayai header IP dari semua sumber pada backend yang dapat diakses langsung. Konfigurasi proxy Vercel/hosting nyata tidak diuji dalam audit lokal ini.
6. **CLI adalah operasi admin lokal.** Belum ada panel admin/auth web. CSV dan model harus berasal dari operator tepercaya; path yang tercantum dalam CSV training tidak merupakan input publik dari endpoint unggahan.
7. **Panggilan Gemini dan akurasi bukan hasil audit ini.** Belum dilakukan pentest jaringan, uji akses akun provider, atau validasi penilaian pangan di lapangan. Retensi salinan yang dikirim ke layanan eksternal tidak dikendalikan cleanup lokal.
8. **Verifikasi bukan transaksi lintas berkas.** CSV dapat berhasil diperbarui sebelum penulisan metadata pending gagal (misalnya disk penuh mendadak). Periksa log dan ulangi verifikasi; hash isi mencegah duplikasi foto.

## Mengulang pemeriksaan

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -W error
python -m pip_audit
python -m scripts.cleanup_dataset
```

Perintah terakhir hanya pratinjau. Gunakan `--apply` untuk menghapus pending kedaluwarsa sesuai kebijakan yang dikonfigurasi. `pip-audit` menghubungi layanan advisori dengan nama dan versi dependensi.
