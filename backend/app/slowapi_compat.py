"""Kompatibilitas SlowAPI 0.1.10 dengan pemeriksaan coroutine Python modern.

Upstream masih memakai asyncio.iscoroutinefunction pada limit/exempt.
Alihkan referensi asyncio milik SlowAPI saja; modul stdlib tidak diubah.
Hapus adapter setelah memperbarui SlowAPI ke rilis yang sudah memakai inspect.
"""
import asyncio
import inspect

import slowapi.extension as extension
from slowapi import Limiter


class _AsyncioCompat:
    iscoroutinefunction = staticmethod(inspect.iscoroutinefunction)

    def __getattr__(self, name):
        return getattr(asyncio, name)


extension.asyncio = _AsyncioCompat()

__all__ = ['Limiter']
