from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_returns_ok_with_the_database_up() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    # P9: it told any caller which deployment they had reached.
    assert "environment" not in data


def test_health_is_unavailable_with_the_database_down() -> None:
    """P9 (M-10): it answered "ok" with Postgres down."""

    class _Down:
        async def __aenter__(self):
            raise ConnectionRefusedError("database down")

        async def __aexit__(self, *exc):
            return False

    with patch("api.routes.health.AsyncSessionLocal", new=lambda: _Down()):
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
