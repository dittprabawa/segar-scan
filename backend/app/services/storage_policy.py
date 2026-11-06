"""Batas penyimpanan lokal; nilai konfigurasi harus positif."""
import os
from pathlib import Path


class StorageFull(ValueError):
    pass


def positive(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f'{name} harus lebih besar dari nol')
    return value


def usage(root):
    # Jangan mengikuti symlink ke data di luar direktori yang dikelola.
    size = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                size += path.stat().st_size
    return size


def check_capacity(root, extra, variable, default_mb):
    if usage(root) + extra > positive(variable, default_mb) * 1024 * 1024:
        raise StorageFull(f'Batas {variable} tercapai; penambahan data dihentikan')
