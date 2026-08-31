import time
from fastapi.testclient import TestClient
import pytest

from deepseek_megapack.main import app
from deepseek_megapack.token_store import TokenStore, token_store


@pytest.fixture(autouse=True)
def clean_token_store():
    token_store.clear()
    yield
    token_store.clear()


def test_token_store_unit_roundtrip_66_scenes():
    store = TokenStore(default_ttl=3600.0)
    scene_ids = list(range(1, 67))  # 66 scene IDs: 1 to 66
    token = store.create_token(scene_ids)

    assert isinstance(token, str)
    assert len(token) >= 32

    retrieved = store.get_token(token)
    assert retrieved == scene_ids
    assert len(retrieved) == 66


def test_token_store_validation():
    store = TokenStore()

    # Empty list
    with pytest.raises(ValueError, match="non-empty"):
        store.create_token([])

    # Negative ID
    with pytest.raises(ValueError, match="positive integer"):
        store.create_token([1, -5, 3])

    # Zero ID
    with pytest.raises(ValueError, match="positive integer"):
        store.create_token([0])

    # Non-integer / boolean
    with pytest.raises(ValueError, match="positive integer"):
        store.create_token([True, 2])

    with pytest.raises(ValueError, match="positive integer"):
        store.create_token(["1", 2])


def test_token_store_ttl_expiration():
    store = TokenStore(default_ttl=60.0)
    start_time = 1000.0

    token = store.create_token([1, 2, 3], current_time=start_time)
    assert len(store) == 1

    # Before TTL expires (59 seconds later)
    assert store.get_token(token, current_time=start_time + 59.0) == [1, 2, 3]

    # After TTL expires (61 seconds later)
    assert store.get_token(token, current_time=start_time + 61.0) is None
    assert len(store) == 0


def test_token_store_sweep_expired():
    store = TokenStore(default_ttl=60.0)
    start_time = 1000.0

    t1 = store.create_token([1], current_time=start_time)
    t2 = store.create_token([2], current_time=start_time + 30.0)

    # At 1070.0, t1 is expired (70s old > 60s), t2 is still valid (40s old < 60s)
    removed = store.sweep_expired(current_time=start_time + 70.0)
    assert removed == 1
    assert store.get_token(t1, current_time=start_time + 70.0) is None
    assert store.get_token(t2, current_time=start_time + 70.0) == [2]


def test_token_store_unknown_token():
    store = TokenStore()
    assert store.get_token("nonexistent-token-12345") is None


def test_api_token_post_and_get_66_scenes():
    client = TestClient(app)
    scene_ids = list(range(1, 67))  # 66 scenes

    # POST /api/token
    res = client.post("/api/token", json={"sceneIds": scene_ids})
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    token = data["token"]
    assert isinstance(token, str) and len(token) >= 32

    # GET /api/token/{token}
    res_get = client.get(f"/api/token/{token}")
    assert res_get.status_code == 200
    assert res_get.json() == {"sceneIds": scene_ids}


def test_api_token_invalid_payloads():
    client = TestClient(app)

    # Empty list
    res = client.post("/api/token", json={"sceneIds": []})
    assert res.status_code in (400, 422)

    # Non-integer IDs
    res = client.post("/api/token", json={"sceneIds": ["abc"]})
    assert res.status_code in (400, 422)

    # Boolean ID in list (should be rejected with 422)
    res = client.post("/api/token", json={"sceneIds": [True, 2]})
    assert res.status_code == 422

    # Negative ID
    res = client.post("/api/token", json={"sceneIds": [-1, 2]})
    assert res.status_code in (400, 422)

    # Over 200 scenes
    res = client.post("/api/token", json={"sceneIds": list(range(1, 202))})
    assert res.status_code in (400, 422)


def test_token_store_max_limit():
    store = TokenStore()
    with pytest.raises(ValueError, match="max 200"):
        store.create_token(list(range(1, 202)))


def test_token_store_format_validation():
    store = TokenStore()
    # Special characters / punctuation
    assert store.get_token("invalid-token-with-dashes-and-spaces!") is None
    # Too long (> 64 chars)
    assert store.get_token("a" * 65) is None
    # Empty token
    assert store.get_token("") is None



def test_api_token_not_found():
    client = TestClient(app)
    res = client.get("/api/token/unknown-token-uuid")
    assert res.status_code == 404
    assert "Token not found" in res.json()["detail"]


def test_api_token_cors_headers():
    client = TestClient(app)
    # Check preflight or request from localhost:9999 and 127.0.0.1:9999
    res = client.options(
        "/api/token",
        headers={
            "Origin": "http://127.0.0.1:9999",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:9999"


def test_api_token_rate_limiting():
    client = TestClient(app)
    # First 10 requests should succeed
    for _ in range(10):
        res = client.post("/api/token", json={"sceneIds": [1, 2]})
        assert res.status_code == 200

    # 11th request within 60s should be 429
    res_overflow = client.post("/api/token", json={"sceneIds": [1, 2]})
    assert res_overflow.status_code == 429
    assert "Rate limit" in res_overflow.json()["detail"]


def test_token_store_rate_limit_unit():
    store = TokenStore()
    now = 1000.0
    # 10 calls from ip-1 at now
    for _ in range(10):
        assert store.check_rate_limit("1.2.3.4", max_requests=10, window_seconds=60.0, current_time=now) is True

    # 11th call fails
    assert store.check_rate_limit("1.2.3.4", max_requests=10, window_seconds=60.0, current_time=now) is False

    # Different IP succeeds
    assert store.check_rate_limit("5.6.7.8", max_requests=10, window_seconds=60.0, current_time=now) is True

    # After window (61s later) original IP succeeds again
    assert store.check_rate_limit("1.2.3.4", max_requests=10, window_seconds=60.0, current_time=now + 61.0) is True

