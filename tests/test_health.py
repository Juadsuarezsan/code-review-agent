from fastapi.testclient import TestClient
from src.api.main import app


def test_health_returns_ok():
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
