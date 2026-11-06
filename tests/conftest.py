from pathlib import Path
import sys
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(autouse=True)
def isolated_dataset_pending(tmp_path, monkeypatch):
    """Tes API tidak boleh menulis foto ke dataset pengguna atau cloud saat testing."""
    monkeypatch.setenv('DATASET_PENDING_DIR', str(tmp_path / 'pending'))
    monkeypatch.setenv('STORAGE_BACKEND', 'local')
