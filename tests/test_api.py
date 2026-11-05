from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"