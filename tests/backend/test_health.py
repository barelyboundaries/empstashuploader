from fastapi.testclient import TestClient

from empornium_megapack.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["track"] == "DeepSeek"
    assert body["staging_dir"].endswith("runtime\\staging")
