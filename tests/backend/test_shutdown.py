"""Tests for the graceful shutdown endpoint (POST /api/shutdown)."""

from fastapi.testclient import TestClient

from empornium_megapack.main import app

client = TestClient(app)


def test_shutdown_endpoint_returns_200():
    response = client.post("/api/shutdown")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "detail": "Server shutting down"}


def test_shutdown_endpoint_foreign_host_rejected():
    bad_client = TestClient(app, headers={"Host": "evil.com"})
    response = bad_client.post("/api/shutdown")
    assert response.status_code == 403
    assert response.json()["detail"] == "Untrusted Host header"


def test_shutdown_endpoint_loopback_hosts_allowed():
    for host in ["127.0.0.1:9941", "localhost:9941", "[::1]:9941"]:
        c = TestClient(app, headers={"Host": host})
        resp = c.post("/api/shutdown")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["detail"] == "Server shutting down"
