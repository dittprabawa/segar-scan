import io
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from limits import parse
from PIL import Image

from backend.app import main
from backend.app.main import app
from backend.app.services import gambar, vlm
from backend.app.services.statistik import Statistik

client = TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("aktif", ["0", "1"])
def test_health(monkeypatch, aktif):
    kunci = "kunci-rahasia-tiruan-health"
    monkeypatch.setenv("GEMINI_API_KEY", kunci)
    monkeypatch.setenv("USE_VLM", aktif)

    def jangan_panggil_gemini():
        pytest.fail("Health tidak boleh membuat client Gemini")

    monkeypatch.setattr(vlm, "_get_client", jangan_panggil_gemini)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "versi": app.version,
        "versi_prompt": vlm.PROMPT_VERSION,
        "statistik": Statistik().ringkasan(),
        "gemini": aktif == "1",
        "model_lokal": False,
        "siap": aktif == "1",
    }
    assert response.json()["versi"] == client.get("/openapi.json").json()["info"]["version"]
    assert kunci not in response.text


# Semua request melewati routing, validasi, decoder, dan inference asli.
# Hanya panggilan layanan Gemini yang diganti agar tes bebas jaringan/kuota.
@pytest.fixture(autouse=True)
def lingkungan_api(monkeypatch):
    monkeypatch.setattr(app.state, "statistik", Statistik())
    monkeypatch.setenv("GEMINI_API_KEY", "kunci-tiruan")
    monkeypatch.setenv("USE_VLM", "1")
    monkeypatch.setattr(main.limiter, "enabled", False)
    main.limiter.reset()
    monkeypatch.setattr(
        vlm, "_get_client",
        lambda: pytest.fail("Tes API tidak boleh menghubungi Gemini"),
    )
    yield
    main.limiter.reset()


@pytest.fixture
def gemini(monkeypatch):
    mock = Mock(return_value={
        "valid": True, "jenis": "bayam", "skor": 85.0,
        "skor_min": 80.0, "skor_maks": 90.0,
        "keyakinan": "tinggi", "ciri": "Daun hijau dan tegak.",
        "model": "model-tiruan",
    })
    monkeypatch.setattr(vlm, "nilai", mock)
    return mock


def png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def foto():
    rng = np.random.default_rng(42)
    return png(Image.fromarray(rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)))


def kirim(data):
    return client.post("/predict", files={"file": ("foto.png", data, "image/png")})


def test_predict_sukses(foto, gemini):
    response = kirim(foto)
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "skor": 85.0, "skor_min": 80.0, "skor_maks": 90.0,
        "label": "Sangat segar", "jenis": "bayam", "keyakinan": "tinggi",
        "saran": "Layak dibeli", "ciri": "Daun hijau dan tegak.",
        "alasan": None, "sumber": "gemini",
    }
    gemini.assert_called_once()
    assert gemini.call_args.kwargs["deadline"] is not None


def test_predict_penolakan_gemini(foto, gemini):
    gemini.return_value = {"valid": False, "alasan": "Foto bukan sayuran."}
    response = kirim(foto)
    assert response.status_code == 200
    hasil = response.json()
    assert hasil["status"] == "ditolak"
    assert hasil["alasan"] == "Foto bukan sayuran."
    assert hasil["skor"] is None
    assert hasil["sumber"] is None


@pytest.mark.parametrize("warna,alasan", [(5, "gelap"), (120, "buram"), (255, "terang")])
def test_predict_qc_menolak_sebelum_gemini(gemini, warna, alasan):
    response = kirim(png(Image.new("RGB", (200, 200), (warna,) * 3)))
    assert response.status_code == 200
    assert response.json()["status"] == "ditolak"
    assert alasan in response.json()["alasan"]
    gemini.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("layanan gagal"), TimeoutError("timeout")])
def test_predict_gemini_gagal_503(foto, gemini, error):
    gemini.side_effect = error
    response = kirim(foto)
    assert response.status_code == 503
    assert response.json() == {"detail": "Penilai sedang tidak tersedia. Coba lagi sebentar."}
    assert response.headers["retry-after"] == "30"


@pytest.mark.parametrize("tanpa_kunci", [True, False])
def test_predict_penilai_tidak_tersedia_503(monkeypatch, foto, gemini, tanpa_kunci):
    if tanpa_kunci:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("USE_VLM", "0")
    response = kirim(foto)
    assert response.status_code == 503
    assert response.json() == {"detail": "Model belum tersedia"}
    assert response.headers["retry-after"] == "30"
    gemini.assert_not_called()


def test_predict_bukan_gambar_400(gemini):
    response = kirim(b"ini bukan gambar")
    assert response.status_code == 400
    assert response.json() == {"detail": "File bukan gambar yang valid"}
    gemini.assert_not_called()


@pytest.mark.parametrize("kelebihan", [1, 128 * 1024])
def test_predict_file_besar_413(gemini, kelebihan):
    # +1 menguji batas pembacaan; +128 KiB menguji Content-Length.
    response = kirim(b"x" * (main.MAKS_BYTES + kelebihan))
    assert response.status_code == 413
    assert response.json() == {"detail": "Foto terlalu besar (maks 5 MB)"}
    gemini.assert_not_called()


def test_predict_piksel_besar_413(monkeypatch, gemini):
    monkeypatch.setattr(gambar, "MAX_PIXELS", 1000)
    response = kirim(png(Image.new("RGB", (40, 40))))
    assert response.status_code == 413
    assert response.json() == {"detail": "Foto terlalu besar. Coba foto ulang."}
    gemini.assert_not_called()


def test_predict_tanpa_file_422(gemini):
    response = client.post("/predict")
    assert response.status_code == 422
    assert response.json() == {"detail": "Field 'file' wajib dikirim sebagai multipart/form-data."}
    gemini.assert_not_called()


def test_predict_rate_limit_429(monkeypatch, foto, gemini):
    monkeypatch.setattr(main.limiter, "enabled", True)
    jumlah = parse(main.RATE_LIMIT).amount
    for _ in range(jumlah):
        assert kirim(foto).status_code == 200
    response = kirim(foto)
    assert response.status_code == 429
    assert response.json() == {"detail": "Terlalu banyak permintaan. Coba lagi sebentar lagi."}
    assert response.headers["retry-after"] == "60"
    assert gemini.call_count == jumlah


def test_predict_error_tak_terduga_500(monkeypatch, foto, gemini, caplog):
    rahasia = "rahasia-tiruan-jangan-bocor"
    monkeypatch.setattr(main.service, "predict", Mock(side_effect=ValueError(rahasia)))
    response = kirim(foto)
    assert response.status_code == 500
    assert response.json() == {"detail": "Terjadi gangguan pada server. Coba lagi sebentar."}
    assert rahasia not in response.text
    assert rahasia not in caplog.text
    gemini.assert_not_called()


def test_statistik_api(foto, gemini):
    assert kirim(foto).status_code == 200
    gemini.return_value = {"valid": False, "alasan": "Bukan sayur"}
    assert kirim(foto).status_code == 200
    assert kirim(b"bukan gambar").status_code == 400
    assert client.post("/predict").status_code == 422
    hasil = client.get("/health").json()["statistik"]
    assert hasil["jumlah_permintaan"] == 4
    assert hasil["jumlah_ditolak"] == 1
    assert hasil["jumlah_error"] == 2
    assert hasil["persentase_ditolak"] == 25
    assert hasil["rata_rata_waktu_ms"] > 0
    assert client.get("/health").json()["statistik"] == hasil


def test_statistik_durasi_dan_konkuren():
    from concurrent.futures import ThreadPoolExecutor
    statistik = Statistik()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: statistik.catat(20, ditolak=True), range(100)))
    statistik.catat(40, error=True)
    hasil = statistik.ringkasan()
    assert hasil["jumlah_permintaan"] == 101
    assert hasil["jumlah_ditolak"] == 100
    assert hasil["jumlah_error"] == 1
    assert hasil["rata_rata_waktu_ms"] == round(2040 / 101, 2)
