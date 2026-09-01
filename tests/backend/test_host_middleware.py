"""Host-header allowlist middleware tests.

The sidecar binds loopback only, but a browser on any origin can still reach
127.0.0.1:9941 (DNS rebinding / cross-origin simple requests). The middleware
rejects any Host whose hostname is not loopback; `testserver` stays allowed
because Starlette's TestClient sends it on every request.
"""

from fastapi.testclient import TestClient

from empornium_megapack.main import app

client = TestClient(app)


def test_default_testserver_host_allowed():
    """TestClient's own Host header (testserver) must stay on the allowlist."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_localhost_with_port_allowed():
    response = client.get("/health", headers={"Host": "localhost:9941"})
    assert response.status_code == 200


def test_loopback_ipv4_with_port_allowed():
    response = client.get("/health", headers={"Host": "127.0.0.1:9941"})
    assert response.status_code == 200


def test_loopback_ipv6_bracket_with_port_allowed():
    response = client.get("/health", headers={"Host": "[::1]:9941"})
    assert response.status_code == 200


def test_uppercase_host_allowed():
    response = client.get("/health", headers={"Host": "LOCALHOST:9941"})
    assert response.status_code == 200


def test_foreign_host_rejected():
    response = client.get("/health", headers={"Host": "evil.com"})
    assert response.status_code == 403
    assert "detail" in response.json()


def test_foreign_host_with_port_rejected():
    response = client.get("/health", headers={"Host": "evil.com:9941"})
    assert response.status_code == 403


def test_rebound_loopback_host_rejected():
    """Classic DNS-rebinding shape: loopback prefixed with a foreign suffix."""
    response = client.get("/health", headers={"Host": "127.0.0.1.evil.com"})
    assert response.status_code == 403


def test_fs_exists_foreign_host_rejected():
    response = client.post(
        "/api/fs/exists", json={"paths": ["C:\\"]}, headers={"Host": "evil.com"}
    )
    assert response.status_code == 403


def test_missing_host_header_allowed():
    """HTTP/1.0 clients may omit Host entirely — allow (browsers always send it)."""
    import asyncio

    async def call_asgi_without_host():
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.0",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"",
            "root_path": "",
            "headers": [],  # no Host header at all
            "client": ("127.0.0.1", 51000),
            "server": ("127.0.0.1", 9941),
        }
        await app(scope, receive, send)
        return messages

    messages = asyncio.run(call_asgi_without_host())
    start = messages[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
