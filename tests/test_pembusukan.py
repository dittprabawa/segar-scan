from unittest.mock import Mock

from PIL import Image
import pytest

from backend.app.services import inference


@pytest.fixture
def penilai(monkeypatch):
    monkeypatch.setattr(inference.quality, 'cek_kualitas', lambda image: None)
    monkeypatch.setattr(inference.vlm, 'tersedia', lambda: True)
    mock = Mock(return_value={
        'valid': True, 'jenis': 'wortel', 'skor': 75.0,
        'skor_min': 65.0, 'skor_maks': 85.0, 'keyakinan': 'sedang',
        'ciri': 'Area gelap rusak pada sebagian wortel.',
        'pembusukan': 'jelas', 'model': 'tiruan', 'prompt_versi': 'p3',
    })
    monkeypatch.setattr(inference.vlm, 'nilai', mock)
    return mock


def nilai():
    with Image.new('RGB', (50, 50)) as image:
        return inference.InferenceService().predict(image)


def test_busuk_tidak_boleh_mendapat_saran_segar(penilai):
    result = nilai()
    assert result['status'] == 'ok'
    assert result['label'] == 'Indikasi busuk'
    assert 0 <= result['skor_min'] <= result['skor'] <= result['skor_maks'] <= 19
    assert 'Sebaiknya dihindari' in result['saran']
    assert result['jenis'] == 'wortel'


@pytest.mark.parametrize('pembusukan', ['meragukan', 'tidak valid', None])
def test_tanda_ambigu_tidak_diberi_skor(penilai, pembusukan):
    penilai.return_value['pembusukan'] = pembusukan
    result = nilai()
    assert result['status'] == 'ditolak'
    assert result['skor'] is None
    assert 'foto dekat' in result['alasan']


def test_field_hilang_tidak_dianggap_segar(penilai):
    del penilai.return_value['pembusukan']
    assert nilai()['status'] == 'ditolak'


def test_tanpa_tanda_busuk_mempertahankan_skor(penilai):
    penilai.return_value.update(pembusukan='tidak_terlihat', ciri='Permukaan utuh.')
    result = nilai()
    assert result['status'] == 'ok'
    assert result['skor'] == 75
    assert result['label'] == 'Segar'


@pytest.mark.parametrize('jenis', ['daging ayam', 'daging sapi', 'daging kambing', 'daging (jenis belum pasti)'])
@pytest.mark.parametrize('skor', [25, 50, 85])
def test_daging_memakai_penilaian_visual(penilai, jenis, skor):
    penilai.return_value.update(jenis=jenis, kategori='daging', skor=skor,
                               skor_min=skor-5, skor_maks=skor+5,
                               pembusukan='tidak_terlihat')
    result = nilai()
    assert result['status'] == 'ok'
    assert result['jenis'] == jenis
    assert result['label'] not in {'Layu', 'Segera olah', 'Segar', 'Sangat segar'}
    assert 'tidak dapat dipastikan dari foto' in result['saran']


def test_daging_busuk_tetap_dibatasi(penilai):
    penilai.return_value.update(jenis='daging ayam', kategori='daging', pembusukan='jelas')
    result = nilai()
    assert result['label'] == 'Indikasi busuk'
    assert result['skor_maks'] <= 19


def test_daging_tanda_meragukan_minta_foto_ulang(penilai):
    penilai.return_value.update(jenis='daging sapi', kategori='daging', pembusukan='meragukan')
    result = nilai()
    assert result['status'] == 'ditolak'
    assert result['skor'] is None
