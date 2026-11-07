"""Statistik prediksi dalam memori, per proses."""

from threading import Lock


class Statistik:
    def __init__(self):
        self._lock = Lock()
        self._jumlah = self._ditolak = self._error = 0
        self._durasi = 0.0

    def catat(self, durasi_ms, ditolak=False, error=False):
        with self._lock:
            self._jumlah += 1
            self._ditolak += int(ditolak)
            self._error += int(error)
            self._durasi += durasi_ms

    def ringkasan(self):
        with self._lock:
            n = self._jumlah
            return {
                "cakupan": "POST /predict; per proses sejak startup",
                "jumlah_permintaan": n,
                "jumlah_ditolak": self._ditolak,
                "jumlah_error": self._error,
                "persentase_ditolak": round(100 * self._ditolak / n, 2) if n else 0.0,
                "rata_rata_waktu_ms": round(self._durasi / n, 2) if n else 0.0,
            }
