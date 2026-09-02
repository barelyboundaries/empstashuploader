"""
Adversarial empirical verification and stress testing for Backend Run Store & Endpoints.
Challenger 1 verification suite covering:
1. Concurrency & Capacity Bounding (>1000 items, exact 10% eviction, multi-threaded stress)
2. TTL Expiration & Sweep Edge Cases (zero TTL, negative TTL, clock skew, exact boundaries)
3. POST /api/run/{run_id} Fuzzing & Payload Limits (regex edge cases, 2MB boundary, non-JSON/non-dict)
4. GET /api/run/{run_id} Handling (unknown IDs, invalid IDs, traversal attempts, expired IDs)
5. Host Header Middleware Enforcement (DNS rebinding attacks, spoofed ports/hosts, bracket forms)
6. Announce URL Masking Defense-in-Depth & Non-Corruption of other fields
"""

import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(autouse=True)
def clean_run_store():
    """Reset global run_store before and after each test."""
    run_store.clear()
    yield
    run_store.clear()


@pytest.fixture
def client():
    return TestClient(app, headers={"Host": "127.0.0.1:9941"})


# ==============================================================================
# 1. Concurrency & Capacity Bounding (>1000 items)
# ==============================================================================

def test_run_store_exact_capacity_1000_and_10_percent_eviction():
    """Verify RunStore at 1000 items triggers exactly 10% (100 items) eviction on 1001st."""
    store = RunStore(default_ttl=3600.0, max_entries=1000)
    t0 = 1000000.0

    # Insert 1000 entries
    for i in range(1000):
        store.store_run(f"run-{i:04d}", {"index": i}, current_time=t0 + i)

    assert len(store) == 1000
    # All 1000 exist
    assert store.get_run("run-0000", current_time=t0 + 1000) == {"index": 0}
    assert store.get_run("run-0999", current_time=t0 + 1000) == {"index": 999}

    # Insert 1001st entry -> Should evict oldest 100 entries (run-0000 through run-0099)
    store.store_run("run-1000", {"index": 1000}, current_time=t0 + 1000)

    # Capacity should now be 1000 - 100 + 1 = 901
    assert len(store) == 901

    # First 100 entries must be evicted
    for i in range(100):
        assert store.get_run(f"run-{i:04d}", current_time=t0 + 1001) is None

    # Next 900 entries + the new 1001st entry must exist
    for i in range(100, 1000):
        assert store.get_run(f"run-{i:04d}", current_time=t0 + 1001) == {"index": i}
    assert store.get_run("run-1000", current_time=t0 + 1001) == {"index": 1000}


def test_run_store_massive_sequential_insertions():
    """Verify RunStore never exceeds max_entries during 2500 continuous insertions."""
    store = RunStore(default_ttl=3600.0, max_entries=1000)
    t0 = 1000000.0

    for i in range(2500):
        store.store_run(f"run-{i}", {"index": i}, current_time=t0 + i)
        assert len(store) <= 1000

    # Final length must be <= 1000
    assert 900 <= len(store) <= 1000
    # Latest entry must be present
    assert store.get_run("run-2499", current_time=t0 + 2500) == {"index": 2499}
    # Very early entries must be evicted
    assert store.get_run("run-0", current_time=t0 + 2500) is None


def test_run_store_multithreaded_concurrency_stress():
    """Verify thread safety under heavy concurrent store, get, and sweep operations."""
    store = RunStore(default_ttl=10.0, max_entries=500)
    errors = []

    def worker_store(worker_id: int):
        try:
            for i in range(100):
                run_id = f"w{worker_id}_{i}"
                store.store_run(run_id, {"worker": worker_id, "i": i}, ttl=5.0)
                time.sleep(0.0001)
        except Exception as exc:
            errors.append(f"Store error: {exc}")

    def worker_read():
        try:
            for _ in range(100):
                store.get_run("w0_0")
                store.sweep_expired()
                time.sleep(0.0001)
        except Exception as exc:
            errors.append(f"Read error: {exc}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for w in range(6):
            futures.append(executor.submit(worker_store, w))
        for _ in range(2):
            futures.append(executor.submit(worker_read))

        concurrent.futures.wait(futures)

    assert errors == [], f"Concurrency errors encountered: {errors}"
    assert len(store) <= 500


# ==============================================================================
# 2. TTL Expiration & Sweep Edge Cases
# ==============================================================================

def test_run_store_zero_ttl():
    """Verify zero TTL expires immediately once timestamp advances."""
    store = RunStore()
    t0 = 1000.0

    store.store_run("run-zero-ttl", {"data": 1}, ttl=0.0, current_time=t0)
    # At exact same timestamp t0, now - created_at == 0.0 (not > 0.0)
    assert store.get_run("run-zero-ttl", current_time=t0) == {"data": 1}

    # At t0 + 0.001s, 0.001 > 0.0 -> expired!
    assert store.get_run("run-zero-ttl", current_time=t0 + 0.001) is None
    assert len(store) == 0


def test_run_store_negative_ttl():
    """Verify negative TTL expires immediately even at the creation timestamp."""
    store = RunStore()
    t0 = 1000.0

    store.store_run("run-neg-ttl", {"data": 1}, ttl=-1.0, current_time=t0)
    # At t0: now - created_at = 0.0 > -1.0 -> Expired immediately on get
    assert store.get_run("run-neg-ttl", current_time=t0) is None
    assert len(store) == 0


def test_run_store_future_clock_skew():
    """Verify behavior when created_at is in the future due to NTP / clock adjustments."""
    store = RunStore(default_ttl=3600.0)
    t0 = 1000.0

    # Clock was at 1100 when stored
    store.store_run("run-future", {"data": 1}, current_time=t0 + 100.0)

    # Clock stepped back to t0 (1000)
    # now - created_at = 1000 - 1100 = -100 <= 3600 -> Not expired
    assert store.get_run("run-future", current_time=t0) == {"data": 1}

    # Clock reaches 1100 + 3600.5 -> expired
    assert store.get_run("run-future", current_time=t0 + 100.0 + 3600.5) is None


def test_run_store_exact_ttl_boundary():
    """Verify strict inequality: now - created_at > ttl."""
    store = RunStore()
    t0 = 1000.0
    ttl = 10.0

    store.store_run("run-boundary", {"data": 1}, ttl=ttl, current_time=t0)

    # At exact boundary t0 + 10.0: now - created_at = 10.0. 10.0 > 10.0 is False.
    assert store.get_run("run-boundary", current_time=t0 + 10.0) == {"data": 1}

    # At t0 + 10.000001: 10.000001 > 10.0 is True -> expired!
    assert store.get_run("run-boundary", current_time=t0 + 10.000001) is None


# ==============================================================================
# 3. POST /api/run/{run_id} Fuzzing & Payload Limits
# ==============================================================================

@pytest.mark.parametrize(
    "valid_id",
    [
        "a",
        "Z",
        "0",
        "9",
        "_",
        "-",
        "a" * 64,
        "A-B_123",
        "run_2026_09_02_job_9941-build",
    ],
)
def test_post_run_valid_id_fuzzing(client, valid_id):
    """Verify all valid regex permutations match and store successfully."""
    resp = client.post(f"/api/run/{valid_id}", json={"valid": True})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "run_id": valid_id}


@pytest.mark.parametrize(
    "invalid_id",
    [
        "a" * 65,  # 65 chars
        "a" * 200,  # 200 chars
        " space_start",
        "space_end ",
        "space inside",
        "null\x00byte",
        "tab\tinside",
        "newline\ninside",
        "emoji_🚀",
        "unicode_café",
        "dot.dot",
        "colon:run",
        "slash/run",
        "backslash\\run",
        "quote'run",
        'double"quote',
        "semicolon;run",
        "query?param=1",
        "hash#anchor",
        "at@domain",
        "exclamation!run",
        "dollar$run",
        "percent%20run",
    ],
)
def test_post_run_invalid_id_fuzzing(client, invalid_id):
    """Verify invalid run_id formats are rejected with HTTP 400 (or 404 for path separators)."""
    import urllib.parse
    encoded_id = urllib.parse.quote(invalid_id)
    resp = client.post(f"/api/run/{encoded_id}", json={"valid": True})
    # If path separator is used, ASGI routing may 404 before reaching endpoint;
    # all other invalid formats reach the endpoint and return 400.
    assert resp.status_code in (400, 404)
    if resp.status_code == 400:
        assert "Invalid run_id format" in resp.text


def test_post_run_payload_size_2mb_exact_boundary(client):
    """Verify payload of exactly <= 2MB is accepted, while 2MB + 1 byte is rejected."""
    run_id = "test-2mb-boundary"

    # Construct payload of exactly ~2MB
    # {"d": "<str>"} -> 8 bytes overhead
    target_size = 2 * 1024 * 1024
    content = "a" * (target_size - 10)
    valid_payload = json.dumps({"d": content}).encode("utf-8")
    assert len(valid_payload) <= 2 * 1024 * 1024

    resp = client.post(
        f"/api/run/{run_id}",
        content=valid_payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(valid_payload))},
    )
    assert resp.status_code == 200

    # Payload exceeding 2MB by 1 byte
    oversized = "a" * (target_size + 1)
    oversized_payload = json.dumps({"d": oversized}).encode("utf-8")
    resp_over = client.post(
        f"/api/run/{run_id}",
        content=oversized_payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(oversized_payload))},
    )
    assert resp_over.status_code == 400
    assert "Payload exceeds 2MB limit" in resp_over.text


def test_post_run_content_length_spoofing(client):
    """Verify rejection when Content-Length header claims > 2MB or is malformed."""
    run_id = "test-cl-spoof"

    # Content-Length claims 5MB but body is small
    resp = client.post(
        f"/api/run/{run_id}",
        content=b'{"status": "ok"}',
        headers={"Content-Type": "application/json", "Content-Length": "5242880"},
    )
    assert resp.status_code == 400
    assert "Payload exceeds 2MB limit" in resp.text

    # Content-Length is non-numeric
    resp_bad_cl = client.post(
        f"/api/run/{run_id}",
        content=b'{"status": "ok"}',
        headers={"Content-Type": "application/json", "Content-Length": "invalid_int"},
    )
    assert resp_bad_cl.status_code == 400
    assert "Invalid Content-Length header" in resp_bad_cl.text


@pytest.mark.parametrize(
    "bad_body",
    [
        b"",
        b"   ",
        b"{invalid json",
        b"[1, 2, 3]",
        b'"just a string"',
        b"12345",
        b"true",
        b"false",
        b"null",
    ],
)
def test_post_run_non_dict_and_invalid_json(client, bad_body):
    """Verify non-dictionary and malformed JSON payloads return HTTP 400."""
    resp = client.post(
        "/api/run/valid-run-id",
        content=bad_body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# ==============================================================================
# 4. GET /api/run/{run_id} Handling
# ==============================================================================

def test_get_run_unknown_id_returns_200_found_false(client):
    """Verify unknown but valid run_id returns HTTP 200 {"found": False}."""
    resp = client.get("/api/run/valid-nonexistent-id-123")
    assert resp.status_code == 200
    assert resp.json() == {"found": False}


def test_get_run_invalid_id_returns_400(client):
    """Verify invalid run_id returns HTTP 400."""
    resp = client.get("/api/run/invalid%20id%20with%20spaces")
    assert resp.status_code == 400
    assert "Invalid run_id format" in resp.text


def test_get_run_expired_returns_200_found_false(client):
    """Verify expired run returns HTTP 200 {"found": False}."""
    run_store.store_run("expired-run", {"status": "ok"}, ttl=-1.0)
    resp = client.get("/api/run/expired-run")
    assert resp.status_code == 200
    assert resp.json() == {"found": False}


# ==============================================================================
# 5. Host Header Middleware Enforcement
# ==============================================================================

@pytest.mark.parametrize(
    "untrusted_host",
    [
        "evil.com",
        "evil.com:9941",
        "127.0.0.1.evil.com",
        "127.0.0.1.evil.com:9941",
        "localhost.evil.com",
        "attacker.org",
        "192.168.1.1",
        "192.168.1.1:9941",
        "10.0.0.1",
        "0.0.0.0",
        "127.0.0.2",
        "[2001:db8::1]",
        "[2001:db8::1]:9941",
        "subdomain.localhost",
    ],
)
def test_host_header_middleware_rejection(untrusted_host):
    """Verify forbidden Host headers are rejected with HTTP 403."""
    bad_client = TestClient(app, headers={"Host": untrusted_host})
    resp = bad_client.get("/api/run/any-run-id")
    assert resp.status_code == 403
    assert "Untrusted Host header" in resp.text

    resp_post = bad_client.post("/api/run/any-run-id", json={"status": "ok"})
    assert resp_post.status_code == 403
    assert "Untrusted Host header" in resp_post.text


@pytest.mark.parametrize(
    "trusted_host",
    [
        "127.0.0.1",
        "127.0.0.1:9941",
        "127.0.0.1:8080",
        "localhost",
        "localhost:9941",
        "LOCALHOST:9941",
        "LocalHost",
        "[::1]",
        "[::1]:9941",
        "testserver",
    ],
)
def test_host_header_middleware_allowed(trusted_host):
    """Verify trusted loopback Host headers are accepted."""
    good_client = TestClient(app, headers={"Host": trusted_host})
    resp = good_client.get("/api/run/valid-run-id")
    assert resp.status_code == 200
    assert resp.json() == {"found": False}


def test_host_header_middleware_unbracketed_ipv6():
    """Per RFC 7230/3986, IPv6 Host headers must be bracketed [::1].
    Unbracketed ::1 is rejected (403) because rsplit(':', 1) strips after the last colon."""
    client_unbracketed = TestClient(app, headers={"Host": "::1"})
    resp = client_unbracketed.get("/api/run/valid-run-id")
    assert resp.status_code == 403


# ==============================================================================
# 6. Announce URL Masking Defense-in-Depth & Non-Corruption
# ==============================================================================

def test_announce_url_sanitization_defense_in_depth(client):
    """Verify passkey sanitization masks announce_url without corrupting other fields."""
    run_id = "test-announce-defense"
    raw_announce = "http://tracker.empornium.sx:2710/secret_token_1/secret_token_2/announce"
    bbcode_sample = (
        "[b]Title[/b]\n"
        "[url=http://tracker.empornium.sx:2710/secret_token_1/secret_token_2/announce]Link[/url]\n"
        "Some details about the release."
    )

    payload = {
        "status": "success",
        "announce_url": raw_announce,
        "bbcode": bbcode_sample,
        "pack_title": "Pack with announce in name",
        "preflight": {
            "checks": [
                {"name": "announce_check", "detail": raw_announce}
            ]
        },
    }

    resp = client.post(f"/api/run/{run_id}", json=payload)
    assert resp.status_code == 200

    stored = run_store.get_run(run_id)
    assert stored is not None

    # announce_url must be masked
    assert "secret_token_1" not in stored["announce_url"]
    assert "secret_token_2" not in stored["announce_url"]
    assert "xxxxxxxxxxxxxx" in stored["announce_url"]

    # bbcode, pack_title, preflight checks MUST remain exactly as provided (no blanket regex corruption)
    assert stored["bbcode"] == bbcode_sample
    assert stored["pack_title"] == "Pack with announce in name"
    assert stored["preflight"]["checks"][0]["detail"] == raw_announce


def test_announce_url_non_string_or_missing(client):
    """Verify non-string or missing announce_url does not cause server error."""
    # announce_url as None
    resp = client.post("/api/run/run-none-announce", json={"announce_url": None, "status": "ok"})
    assert resp.status_code == 200
    assert run_store.get_run("run-none-announce") == {"announce_url": None, "status": "ok"}

    # announce_url as integer
    resp = client.post("/api/run/run-int-announce", json={"announce_url": 12345, "status": "ok"})
    assert resp.status_code == 200
    assert run_store.get_run("run-int-announce") == {"announce_url": 12345, "status": "ok"}

    # announce_url missing
    resp = client.post("/api/run/run-no-announce", json={"status": "ok"})
    assert resp.status_code == 200
    assert run_store.get_run("run-no-announce") == {"status": "ok"}
