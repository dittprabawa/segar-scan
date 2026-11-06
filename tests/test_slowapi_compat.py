import asyncio
import inspect
import warnings

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from backend.app.slowapi_compat import Limiter
import slowapi.extension as extension


def test_adapter_tidak_mengubah_stdlib():
    assert extension.asyncio is not asyncio
    assert extension.asyncio.iscoroutinefunction is inspect.iscoroutinefunction
    assert getattr(asyncio, "iscoroutinefunction", None) is not inspect.iscoroutinefunction
    assert extension.asyncio.sleep is asyncio.sleep


def test_decorator_sync_async_dan_exempt_tanpa_deprecation():
    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_error(request, exc):
        return JSONResponse({'detail': 'rate limit'}, status_code=429)

    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)

        @app.get('/sync')
        @limiter.limit('1/minute')
        def sync_endpoint(request: Request):
            return {'ok': True}

        @app.get('/async')
        @limiter.limit('1/minute')
        async def async_endpoint(request: Request):
            return {'ok': True}

        @limiter.exempt
        async def exempt_async():
            return 'async'

        @limiter.exempt
        def exempt_sync():
            return 'sync'

    assert not inspect.iscoroutinefunction(sync_endpoint)
    assert inspect.iscoroutinefunction(async_endpoint)
    assert asyncio.run(exempt_async()) == 'async'
    assert exempt_sync() == 'sync'
    with TestClient(app) as client:
        for path in ['/sync', '/async']:
            assert client.get(path).status_code == 200
            assert client.get(path).status_code == 429
