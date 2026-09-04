"""
Unit, integration, and endpoint tests for the Run Store and Sidecar Run Endpoints.
Covers:
- RunStore in-memory bounded storage, TTL sweeps, eviction, validation, and singleton.
- FastAPI /api/run/{run_id} GET and POST endpoints (validation, 2MB limit, defense-in-depth sanitization).
- Host-header middleware protection on /api/run/{run_id}.
- Plugin task.py post_result_to_sidecar behavior (timeout, port resolution, un-truncated BBCode, error handling).
"""

import json
import os
import sys
import time
import io
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

import pytest
from fastapi.testclient import TestClient

# Ensure project and backend paths are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
PLUGIN_DIR = PROJECT_ROOT / "plugin"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from empornium_megapack.run_store import RunStore, run_store, RUN_ID_REGEX
from empornium_megapack.main import app
import task as task_module


# ==============================================================================
# 1. RunStore Unit Tests
# ==============================================================================

def test_run_store_store_and_get():
    """Verify basic storing and retrieving of task results."""
    store = RunStore(default_ttl=3600.0)
    run_id = "test-run-123"
    result_data = {"status": "success", "pack_title": "Test Pack", "torrent_path": "/path/to/torrent"}

    store.store_run(run_id, result_data)
    retrieved = store.get_run(run_id)

    assert retrieved == result_data
    assert len(store) == 1


def test_run_store_run_id_regex_validation():
    """Verify RUN_ID_REGEX and validation logic in RunStore."""
    store = RunStore()

    # Valid run_ids
    valid_ids = [
        "abc",
        "ABC123_-",
        "run_2026_09_02_001",
        "a" * 64,
        "uuid-1234-5678-90ab",
    ]
    for valid_id in valid_ids:
        assert RUN_ID_REGEX.match(valid_id) is not None
        store.store_run(valid_id, {"val": 1})
        assert store.get_run(valid_id) == {"val": 1}

    # Invalid run_ids for store_run (should raise ValueError)
    invalid_ids = [
        "",
        "a" * 65,
        "run id with spaces",
        "run/with/slash",
        "run?query=1",
        "run:colon",
        "run.dot",
        "run@at",
        12345,
        None,
    ]
    for invalid_id in invalid_ids:
        with pytest.raises(ValueError):
            store.store_run(invalid_id, {"val": 1})
        # get_run should return None for invalid IDs without raising
        assert store.get_run(invalid_id) is None


def test_run_store_result_type_validation():
    """Verify store_run rejects non-dictionary results with ValueError."""
    store = RunStore()
    invalid_results = [
        "a string result",
        ["a", "list"],
        12345,
        None,
        True,
    ]
    for inv in invalid_results:
        with pytest.raises(ValueError, match="must be a dictionary"):
            store.store_run("valid-run-id", inv)


def test_run_store_ttl_expiration_and_sweep():
    """Verify TTL expiration on read and via sweep_expired."""
    store = RunStore(default_ttl=10.0)
    t0 = 1000.0

    store.store_run("run-1", {"id": 1}, ttl=5.0, current_time=t0)
    store.store_run("run-2", {"id": 2}, ttl=15.0, current_time=t0)
    store.store_run("run-3", {"id": 3}, ttl=30.0, current_time=t0)

    assert len(store) == 3

    # At t0 + 6s, run-1 should be expired
    assert store.get_run("run-1", current_time=t0 + 6.0) is None
    assert store.get_run("run-2", current_time=t0 + 6.0) == {"id": 2}
    assert store.get_run("run-3", current_time=t0 + 6.0) == {"id": 3}

    # At t0 + 20s, run-2 should also be expired
    expired_count = store.sweep_expired(current_time=t0 + 20.0)
    assert expired_count == 1  # run-2 swept (run-1 already removed by get_run)
    assert len(store) == 1
    assert store.get_run("run-3", current_time=t0 + 20.0) == {"id": 3}

    # At t0 + 35s, run-3 expires
    assert store.get_run("run-3", current_time=t0 + 35.0) is None
    assert len(store) == 0


def test_run_store_bounded_capacity_eviction():
    """Verify bounded capacity evicts oldest 10% when max_entries is exceeded."""
    max_entries = 10
    store = RunStore(default_ttl=3600.0, max_entries=max_entries)
    t0 = time.time()

    # Insert 10 entries with strictly increasing created_at timestamps
    for i in range(10):
        store.store_run(f"run-{i}", {"index": i}, current_time=t0 + i)

    assert len(store) == 10

    # Inserting 11th entry should trigger eviction of oldest 10% (max(1, 10 // 10) = 1 entry)
    store.store_run("run-10", {"index": 10}, current_time=t0 + 20.0)
    assert len(store) == 10
    assert store.get_run("run-0", current_time=t0 + 20.0) is None  # Oldest evicted
    assert store.get_run("run-1", current_time=t0 + 20.0) == {"index": 1}
    assert store.get_run("run-10", current_time=t0 + 20.0) == {"index": 10}

    # Updating an existing entry should not trigger eviction
    store.store_run("run-10", {"index": 10, "updated": True}, current_time=t0 + 25.0)
    assert len(store) == 10
    assert store.get_run("run-1", current_time=t0 + 25.0) == {"index": 1}


def test_run_store_clear():
    """Verify clear() empties the store."""
    store = RunStore()
    store.store_run("run-1", {"a": 1})
    store.store_run("run-2", {"b": 2})
    assert len(store) == 2

    store.clear()
    assert len(store) == 0
    assert store.get_run("run-1") is None


def test_run_store_module_singleton():
    """Verify the module-level singleton exists and has correct default configuration."""
    assert isinstance(run_store, RunStore)
    assert run_store.default_ttl == 3600.0
    assert run_store.max_entries == 1000


# ==============================================================================
# 2. FastAPI Endpoint Tests (/api/run/{run_id})
# ==============================================================================

@pytest.fixture(autouse=True)
def clean_run_store():
    """Ensure clean global run_store before and after each test."""
    run_store.clear()
    yield
    run_store.clear()


@pytest.fixture
def client():
    return TestClient(app, headers={"Host": "127.0.0.1:9941"})


def test_post_run_endpoint_success(client):
    """POST /api/run/{run_id} stores payload and returns HTTP 200 ok."""
    run_id = "test-endpoint-run-001"
    payload = {
        "status": "success",
        "pack_title": "Test Title",
        "torrent_path": "C:\\output\\test.torrent",
        "bbcode": "[b]Test BBCode[/b]",
    }

    response = client.post(f"/api/run/{run_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "run_id": run_id}

    # Verify retrieved from store
    stored = run_store.get_run(run_id)
    assert stored == payload


def test_get_run_endpoint_found(client):
    """GET /api/run/{run_id} returns found: true and result for existing run."""
    run_id = "test-endpoint-get-002"
    result = {"status": "success", "scenes": [1, 2, 3]}
    run_store.store_run(run_id, result)

    response = client.get(f"/api/run/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data == {"found": True, "result": result}


def test_get_run_endpoint_not_found(client):
    """GET /api/run/{run_id} returns HTTP 200 with found: false for unknown run."""
    response = client.get("/api/run/non-existent-run-id")
    assert response.status_code == 200
    data = response.json()
    assert data == {"found": False}


def test_post_and_get_run_invalid_id(client):
    """Endpoints return HTTP 400 when run_id fails regex validation."""
    invalid_ids = [
        "invalid id with spaces",
        "invalid@character",
        "a" * 65,  # > 64 chars
    ]
    for inv_id in invalid_ids:
        # POST
        post_resp = client.post(f"/api/run/{inv_id}", json={"status": "ok"})
        assert post_resp.status_code == 400
        assert "Invalid run_id format" in post_resp.text

        # GET
        get_resp = client.get(f"/api/run/{inv_id}")
        assert get_resp.status_code == 400
        assert "Invalid run_id format" in get_resp.text


def test_post_run_invalid_payload_types(client):
    """POST /api/run/{run_id} rejects invalid JSON or non-dict payloads with HTTP 400."""
    run_id = "test-invalid-payload"

    # Non-dict JSON (array)
    resp_array = client.post(f"/api/run/{run_id}", json=["item1", "item2"])
    assert resp_array.status_code == 400
    assert "Payload must be a JSON object" in resp_array.text

    # Non-dict JSON (number)
    resp_num = client.post(f"/api/run/{run_id}", json=12345)
    assert resp_num.status_code == 400
    assert "Payload must be a JSON object" in resp_num.text

    # Malformed raw body
    resp_malformed = client.post(
        f"/api/run/{run_id}",
        content=b"{malformed json",
        headers={"Content-Type": "application/json"},
    )
    assert resp_malformed.status_code == 400
    assert "Invalid JSON body" in resp_malformed.text


def test_post_run_payload_size_limit(client):
    """POST /api/run/{run_id} rejects payloads exceeding 2MB limit with HTTP 400."""
    run_id = "test-payload-size-limit"

    # Payload slightly larger than 2MB
    large_str = "x" * (2 * 1024 * 1024 + 100)
    large_payload = {"data": large_str}

    # Test via raw bytes
    raw_bytes = json.dumps(large_payload).encode("utf-8")
    response = client.post(
        f"/api/run/{run_id}",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw_bytes))},
    )
    assert response.status_code == 400
    assert "Payload exceeds 2MB limit" in response.text


def test_post_run_defense_in_depth_announce_sanitization(client):
    """POST /api/run/{run_id} masks passkeys in announce_url field if present."""
    run_id = "test-announce-sanitization"
    raw_announce = "http://tracker.empornium.sx:2710/token1/token2/announce"
    payload = {
        "status": "success",
        "announce_url": raw_announce,
        "bbcode": "Some BBCode with url http://example.com/announce",
    }

    response = client.post(f"/api/run/{run_id}", json=payload)
    assert response.status_code == 200

    stored = run_store.get_run(run_id)
    assert stored is not None
    # announce_url must be masked
    assert "token1" not in stored["announce_url"]
    assert "xxxxxx" in stored["announce_url"]
    # other fields must not have blanket regex corruption
    assert stored["bbcode"] == "Some BBCode with url http://example.com/announce"


def test_run_endpoints_host_header_middleware(client):
    """Verify Host-header middleware enforces loopback protection on /api/run/{run_id}."""
    # Untrusted host
    bad_client = TestClient(app, headers={"Host": "evil.com"})
    resp_post = bad_client.post("/api/run/valid-run-id", json={"status": "ok"})
    assert resp_post.status_code == 403
    assert "Untrusted Host header" in resp_post.text

    resp_get = bad_client.get("/api/run/valid-run-id")
    assert resp_get.status_code == 403
    assert "Untrusted Host header" in resp_get.text


# ==============================================================================
# 3. Task Sidecar POST Integration Tests (plugin/task.py)
# ==============================================================================

def test_post_result_to_sidecar_success(monkeypatch):
    """Verify post_result_to_sidecar sends un-truncated BBCode to sidecar endpoint."""
    run_id = "sidecar-post-success-001"
    payload = {"run_id": run_id}
    large_bbcode = "[b]Large BBCode[/b] " * 10000  # ~210 KB, well over log sentinel 100KB limit
    result = {
        "status": "success",
        "pack_title": "Megapack",
        "bbcode": large_bbcode,
        "submission_payload": {"should": "be excluded"},
        "contact_sheets": ["sheet1.jpg"],
    }

    posted_request = {}

    def mock_urlopen(req, timeout=3.0):
        posted_request["url"] = req.full_url
        posted_request["headers"] = dict(req.headers)
        posted_request["data"] = req.data
        posted_request["timeout"] = timeout
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setenv("EMPORNIUM_PORT", "9941")

    task_module.post_result_to_sidecar(payload, result)

    assert posted_request["url"] == f"http://127.0.0.1:9941/api/run/{run_id}"
    assert posted_request["timeout"] == 3.0
    body = json.loads(posted_request["data"].decode("utf-8"))
    assert body["status"] == "success"
    assert body["bbcode"] == large_bbcode  # Not truncated
    assert "submission_payload" not in body  # Excluded
    assert "contact_sheets" not in body  # Excluded


def test_post_result_to_sidecar_port_resolution(monkeypatch):
    """Verify post_result_to_sidecar respects EMPORNIUM_PORT override."""
    run_id = "sidecar-custom-port"
    payload = {"run_id": run_id}
    result = {"status": "ok"}

    posted_urls = []

    def mock_urlopen(req, timeout=3.0):
        posted_urls.append(req.full_url)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # Test custom port via environment variable
    monkeypatch.setenv("EMPORNIUM_PORT", "9955")
    task_module.post_result_to_sidecar(payload, result)
    assert posted_urls[-1] == f"http://127.0.0.1:9955/api/run/{run_id}"

    # Test fallback on invalid env port
    monkeypatch.setenv("EMPORNIUM_PORT", "not-a-port")
    task_module.post_result_to_sidecar(payload, result)
    assert posted_urls[-1] == f"http://127.0.0.1:9941/api/run/{run_id}"


def test_post_result_to_sidecar_error_handling_non_blocking(monkeypatch):
    """Verify post_result_to_sidecar catches HTTPError, URLError, and Exceptions without raising."""
    run_id = "sidecar-error-test"
    payload = {"run_id": run_id}
    result = {"status": "ok"}

    # 1. URLError (Connection Refused)
    def mock_urlopen_url_err(req, timeout=3.0):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_url_err)
    stderr_buf = io.StringIO()
    with patch.object(sys.stderr, "write", stderr_buf.write):
        task_module.post_result_to_sidecar(payload, result)
    assert "\x01w\x02[Sidecar] Transport error posting run result" in stderr_buf.getvalue()

    # 2. HTTPError (HTTP 500)
    def mock_urlopen_http_err(req, timeout=3.0):
        raise urllib.error.HTTPError("http://127.0.0.1:9941", 500, "Server Error", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_http_err)
    stderr_buf = io.StringIO()
    with patch.object(sys.stderr, "write", stderr_buf.write):
        task_module.post_result_to_sidecar(payload, result)
    assert "\x01w\x02[Sidecar] HTTP 500 posting run result" in stderr_buf.getvalue()


def test_post_result_to_sidecar_skips_when_no_run_id():
    """Verify post_result_to_sidecar does nothing if run_id is missing."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        task_module.post_result_to_sidecar({}, {"status": "ok"})
        task_module.post_result_to_sidecar(None, {"status": "ok"})
        task_module.post_result_to_sidecar({"run_id": ""}, {"status": "ok"})
        mock_urlopen.assert_not_called()
