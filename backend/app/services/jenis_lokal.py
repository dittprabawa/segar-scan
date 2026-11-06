"""Muat ulang model jenis ketika hasil retraining berubah."""
from functools import lru_cache
import os
from pathlib import Path

from ml.jenis import DEFAULT_MODEL, ModelJenis


@lru_cache(maxsize=2)
def _load(path, timestamp, size):
    return ModelJenis.load(path)


def predict(image):
    if os.getenv('USE_LOCAL_JENIS', '1') != '1':
        return None
    path = Path(os.getenv('LOCAL_JENIS_MODEL', str(DEFAULT_MODEL)))
    if not path.is_file():
        return None
    stat = path.stat()
    return _load(str(path.resolve()), stat.st_mtime_ns, stat.st_size).predict(image)
