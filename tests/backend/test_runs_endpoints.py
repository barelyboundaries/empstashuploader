"""
Tests for sidecar run listing and deletion endpoints:
- GET /api/runs?limit=50 (newest-first summaries, bandwidth preservation, limit param)
- DELETE /api/run/{run_id} (disk and memory pruning, 200 ok / 404 not found)
- Path traversal rejection (HTTP 400 before filesystem access)
- Host restriction middleware enforcement (HTTP 403 on untrusted Host header)
- CORS preflight and origin verification
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from empornium_megapack.main import app
from empornium_megapack.run_store import run_store, RUN_ID_REGEX
from empornium_megapack.config import get_settings


@pytest.fixture
def client():
    """TestClient with allowed loopback Host header."""
    return TestClient(app, headers={"Host": "127.0.0.1:9941"})


@pytest.fixture(autouse=True)
def isolate_runs_dir(tmp_path, monkeypatch):
    """Ensure run_store and config use an isolated temporary directory for all tests."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_store, "runs_dir", runs_dir)
    settings = get_settings()
    monkeypatch.setattr(settings, "runs_dir", runs_dir)
    run_store.clear(clear_disk=True)
    yield runs_dir
    run_store.clear(clear_disk=True)


def test_get_runs_empty(client):
    """GET /api/runs returns an empty list when no runs exist."""
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_get_runs_newest_first_summaries(client):
    """GET /api/runs returns summaries sorted newest first by created_at."""
    t0 = 1000.0
    payload_old = {
        "status": "success",
        "task": "build",
        "mode": "megapack",
        "pack_title": "Oldest Pack",
        "torrent_path": "/torrents/old.torrent",
        "contact_sheets": ["sheet1.jpg"],
        "bbcode": "[b]Large BBCode[/b] " * 500,
    }
    payload_mid = {
        "status": "failed",
        "task": "build",
        "mode": "single_scene",
        "pack_title": "Middle Pack (Failed)",
        "torrent_path": "",
        "error": "Build failed on timeout",
        "bbcode": "",
    }
    payload_new = {
        "status": "success",
        "task": "build",
        "mode": "megapack",
        "pack_title": "Newest Pack",
        "torrent_path": "/torrents/new.torrent",
        "contact_sheets": ["sheet1.jpg", "sheet2.jpg", "sheet3.jpg"],
        "bbcode": "[b]Large BBCode 3[/b] " * 500,
    }

    run_store.store_run("run-old", payload_old, current_time=t0, persist=True)
    run_store.store_run("run-mid", payload_mid, current_time=t0 + 100, persist=True)
    run_store.store_run("run-new", payload_new, current_time=t0 + 200, persist=True)

    response = client.get("/api/runs")
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 3

    # Ordering: newest first
    assert runs[0]["run_id"] == "run-new"
    assert runs[1]["run_id"] == "run-mid"
    assert runs[2]["run_id"] == "run-old"

    # Summary fields projection check
    assert runs[0]["pack_title"] == "Newest Pack"
    assert runs[0]["ready"] is True
    assert runs[0]["torrent_path"] == "/torrents/new.torrent"
    assert runs[0]["image_count"] == 3
    assert runs[0]["task"] == "build"
    assert runs[0]["mode"] == "megapack"

    # Failed run summary
    assert runs[1]["pack_title"] == "Middle Pack (Failed)"
    assert runs[1]["ready"] is False
    assert runs[1]["mode"] == "single_scene"

    # High-bandwidth fields omitted from summaries
    for r in runs:
        assert "bbcode" not in r
        assert "submission_payload" not in r
        assert "contact_sheets" not in r


def test_get_runs_limit_query_param(client):
    """GET /api/runs?limit=N respects limit and handles default."""
    t0 = 2000.0
    for i in range(10):
        run_store.store_run(
            f"run-{i:02d}",
            {"status": "success", "pack_title": f"Pack {i}"},
            current_time=t0 + i,
            persist=True,
        )

    # Test limit=3
    res_3 = client.get("/api/runs?limit=3")
    assert res_3.status_code == 200
    items_3 = res_3.json()
    assert len(items_3) == 3
    assert items_3[0]["run_id"] == "run-09"
    assert items_3[1]["run_id"] == "run-08"
    assert items_3[2]["run_id"] == "run-07"

    # Test default limit (returns all 10 since < 50)
    res_default = client.get("/api/runs")
    assert res_default.status_code == 200
    assert len(res_default.json()) == 10

    # Test invalid limit parameter handling (non-integer returns 422)
    res_bad = client.get("/api/runs?limit=abc")
    assert res_bad.status_code == 422

    # Test non-positive limit parameter handling (< 1 returns 400)
    res_zero = client.get("/api/runs?limit=0")
    assert res_zero.status_code == 400


def test_delete_run_endpoint_success(client, isolate_runs_dir):
    """DELETE /api/run/{run_id} prunes run from memory and disk, returning 200 OK."""
    run_id = "run-endpoint-delete-001"
    run_store.store_run(run_id, {"pack_title": "Delete Me", "status": "success"}, persist=True)

    disk_file = isolate_runs_dir / f"{run_id}.json"
    assert disk_file.is_file()
    assert run_store.get_run(run_id) is not None

    # DELETE request
    res = client.delete(f"/api/run/{run_id}")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "run_id": run_id}

    # Verified removed on disk
    assert not disk_file.exists()

    # Verified removed in memory
    assert run_store.get_run(run_id) is None

    # Subsequent GET returns not found
    get_res = client.get(f"/api/run/{run_id}")
    assert get_res.status_code == 200
    assert get_res.json() == {"found": False}

    # Subsequent list does not include run
    list_res = client.get("/api/runs")
    assert all(r["run_id"] != run_id for r in list_res.json())


def test_delete_run_endpoint_not_found(client):
    """DELETE /api/run/{run_id} for unknown run returns HTTP 404."""
    res = client.delete("/api/run/non-existent-run-999")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


def test_run_endpoints_path_traversal_rejection(client, isolate_runs_dir):
    """Path traversal in URL parameters for GET and DELETE must return HTTP 400 or 404."""
    traversal_paths = [
        "..%2Fevil",
        "..%2F..%2Fetc%2Fpasswd",
        "..",
        ".",
        "invalid%20with%20spaces",
        "invalid%40char",
        "a" * 65,
    ]

    for bad_path in traversal_paths:
        # GET full result
        get_res = client.get(f"/api/run/{bad_path}")
        assert get_res.status_code in (400, 404), f"GET {bad_path} was not rejected with 400/404"
        if get_res.status_code == 400:
            assert "Invalid run_id format" in get_res.text

        # DELETE
        del_res = client.delete(f"/api/run/{bad_path}")
        assert del_res.status_code in (400, 404), f"DELETE {bad_path} was not rejected with 400/404"
        if del_res.status_code == 400:
            assert "Invalid run_id format" in del_res.text

    # Verify no files created or touched in runs_dir
    assert list(isolate_runs_dir.iterdir()) == []


def test_runs_endpoints_host_restriction_middleware(client):
    """Verify restrict_host_header middleware protects GET /api/runs and DELETE /api/run/{id}."""
    evil_client = TestClient(app, headers={"Host": "evil.com"})

    # GET /api/runs with untrusted host
    res_get = evil_client.get("/api/runs")
    assert res_get.status_code == 403
    assert res_get.json()["detail"] == "Untrusted Host header"

    # DELETE /api/run/{run_id} with untrusted host
    res_del = evil_client.delete("/api/run/valid-run-id")
    assert res_del.status_code == 403
    assert res_del.json()["detail"] == "Untrusted Host header"

    # DNS-rebinding pattern
    rebound_client = TestClient(app, headers={"Host": "127.0.0.1.evil.com"})
    res_rebound = rebound_client.get("/api/runs")
    assert res_rebound.status_code == 403

    # Valid loopback hosts succeed
    for valid_host in ["127.0.0.1:9941", "localhost:9941", "[::1]:9941", "testserver"]:
        loopback_client = TestClient(app, headers={"Host": valid_host})
        res_ok = loopback_client.get("/api/runs")
        assert res_ok.status_code == 200


def test_runs_endpoints_cors_headers(client):
    """Verify CORS preflight and headers allow access from Stash origins."""
    for method in ["GET", "DELETE"]:
        res = client.options(
            "/api/runs" if method == "GET" else "/api/run/test-run",
            headers={
                "Origin": "http://127.0.0.1:9999",
                "Access-Control-Request-Method": method,
            },
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:9999"
