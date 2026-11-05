import pytest
from scripts.evaluasi_jenis import ringkas, baca_dataset


def test_evaluasi_tidak_menghilangkan_penolakan_dan_error():
    rows = [
        dict(jenis_benar=" Bayam ", baseline_status="ok", baseline_jenis="bayam",
             kandidat_status="ditolak", kandidat_jenis=""),
        dict(jenis_benar="caisim", baseline_status="ok", baseline_jenis="bayam",
             kandidat_status="ok", kandidat_jenis="CAISIM"),
        dict(jenis_benar="tomat", baseline_status="error", baseline_jenis="",
             kandidat_status="ok", kandidat_jenis="tomat"),
    ]
    hasil = ringkas(rows)
    assert hasil["baseline"]["akurasi_semua_foto"] == 1 / 3
    assert hasil["kandidat"]["akurasi_semua_foto"] == 2 / 3
    assert hasil["baseline"]["error"] == 1
    assert hasil["kandidat"]["ditolak"] == 1
    assert hasil["baseline"]["jenis_tertukar"] == [{"benar": "caisim", "prediksi": "bayam", "jumlah": 1}]


def test_dataset_kosong_tidak_boleh_dianggap_evaluasi(tmp_path):
    path = tmp_path / "dataset.csv"
    path.write_text("file,jenis_benar,split\n")
    with pytest.raises(ValueError, match="Belum ada foto"):
        baca_dataset(path, "test")
