from fastapi.testclient import TestClient

from empornium_megapack.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["track"] == "Empornium Megapack Builder"
    assert body["version"] == "0.2.0"
    assert "build_stamp" in body
    assert body["staging_dir"].endswith("runtime\\staging")


def test_health_build_stamp_custom_env(monkeypatch):
    # The stamp is resolved once per process now, so a value set after import
    # only takes effect once the cache is dropped. Reset again afterwards so
    # the override cannot leak into later tests.
    from empornium_megapack.main import reset_build_stamp_cache

    monkeypatch.setenv("EMPORNIUM_BUILD_STAMP", "0.2.0-customsha")
    reset_build_stamp_cache()
    try:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["build_stamp"] == "0.2.0-customsha"
        assert body["version"] == "0.2.0"
    finally:
        monkeypatch.delenv("EMPORNIUM_BUILD_STAMP", raising=False)
        reset_build_stamp_cache()


def test_health_build_stamp_frozen_at_process_start(monkeypatch):
    """A BUILD_STAMP rewritten under a running process must not change /health.

    Deploying a new plugin build overwrites BUILD_STAMP while the old sidecar
    is still running. If /health re-read the file, the stale process would
    report the new stamp, and StartBackend's staleness check -- which compares
    /health's build_stamp against the installed stamp -- could never fire.
    """
    from empornium_megapack.main import reset_build_stamp_cache

    monkeypatch.setenv("EMPORNIUM_BUILD_STAMP", "1.0.0-oldbuild")
    reset_build_stamp_cache()
    try:
        assert client.get("/health").json()["build_stamp"] == "1.0.0-oldbuild"

        # A deploy lands underneath the running process. No cache reset here --
        # that is precisely the point.
        monkeypatch.setenv("EMPORNIUM_BUILD_STAMP", "1.0.0-newbuild")

        second = client.get("/health").json()["build_stamp"]
        assert second == "1.0.0-oldbuild", (
            "build_stamp must report the code this process is running, not "
            f"what is on disk now (got {second})"
        )
    finally:
        monkeypatch.delenv("EMPORNIUM_BUILD_STAMP", raising=False)
        reset_build_stamp_cache()


def test_get_build_stamp_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("EMPORNIUM_BUILD_STAMP", raising=False)
    stamp_file = tmp_path / "BUILD_STAMP"
    stamp_file.write_text("0.2.0-filesha\n", encoding="utf-8")

    import empornium_megapack.main as main_mod
    from empornium_megapack.main import get_build_stamp

    orig_path_cls = main_mod.Path

    class FakeFileLocation:
        def resolve(self):
            return self

        @property
        def parent(self):
            return tmp_path

    monkeypatch.setattr(
        main_mod,
        "Path",
        lambda *args: FakeFileLocation() if args and args[0] == main_mod.__file__ else orig_path_cls(*args),
    )
    assert get_build_stamp() == "0.2.0-filesha"


def test_health_and_config_refresh_sources(monkeypatch):
    from empornium_megapack.plugin_settings import clear_cache

    clear_cache()
    # Ensure env vars not set initially
    monkeypatch.delenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", raising=False)
    monkeypatch.delenv("EMPORNIUM_HAMSTER_API_KEY", raising=False)

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "hamster_configured" in data
    assert "hamster_source" in data
    assert "announce_configured" in data
    assert "announce_source" in data
    assert "announce_valid" in data
    assert "announce_invalid_reason" in data
    # Secrets discipline: secret values must NEVER be in response
    assert "hamster_api_key" not in data
    assert "empornium_announce_url" not in data

    # Test POST /api/config/refresh
    refresh_res = client.post("/api/config/refresh")
    assert refresh_res.status_code == 200
    refresh_data = refresh_res.json()
    assert refresh_data["status"] == "ok"
    assert "hamster_configured" in refresh_data
    assert "hamster_source" in refresh_data
    assert "announce_configured" in refresh_data
    assert "announce_source" in refresh_data
    assert "announce_valid" in refresh_data
    assert "announce_invalid_reason" in refresh_data
    assert "hamster_api_key" not in refresh_data
    assert "empornium_announce_url" not in refresh_data


def test_health_and_config_refresh_announce_validity(monkeypatch):
    import json
    from empornium_megapack.plugin_settings import clear_cache
    import empornium_megapack.main as main_mod

    # 1. Unset: announce_valid is False, announce_invalid_reason is empty
    clear_cache()
    monkeypatch.delenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", raising=False)
    orig_url = main_mod.settings.empornium_announce_url
    main_mod.settings.empornium_announce_url = ""
    try:
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["announce_configured"] is False
        assert data["announce_valid"] is False
        assert data["announce_invalid_reason"] == ""

        refresh_res = client.post("/api/config/refresh")
        rdata = refresh_res.json()
        assert rdata["announce_configured"] is False
        assert rdata["announce_valid"] is False
        assert rdata["announce_invalid_reason"] == ""

        # 2. Configured but invalid (e.g. "test")
        monkeypatch.setenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", "test")
        clear_cache()
        res = client.get("/health")
        data = res.json()
        assert data["announce_configured"] is True
        assert data["announce_valid"] is False
        assert data["announce_invalid_reason"] == "Announce URL must use http or https."
        # Secret discipline: ensure input URL "test" is NEVER in response anywhere
        raw_health_str = json.dumps(data)
        assert '"test"' not in raw_health_str
        assert ": \"test\"" not in raw_health_str

        refresh_res = client.post("/api/config/refresh")
        rdata = refresh_res.json()
        assert rdata["announce_configured"] is True
        assert rdata["announce_valid"] is False
        assert rdata["announce_invalid_reason"] == "Announce URL must use http or https."
        raw_refresh_str = json.dumps(rdata)
        assert '"test"' not in raw_refresh_str
        assert ": \"test\"" not in raw_refresh_str

        # 3. Configured and valid
        valid_url = "http://tracker.empornium.sx:2710/secret_token_abc/secret_token_xyz/announce"
        monkeypatch.setenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", valid_url)
        clear_cache()
        res = client.get("/health")
        data = res.json()
        assert data["announce_configured"] is True
        assert data["announce_valid"] is True
        assert data["announce_invalid_reason"] == ""
        # Secret discipline: valid announce URL must never appear in response
        assert valid_url not in json.dumps(data)
        assert "secret_token" not in json.dumps(data)

        refresh_res = client.post("/api/config/refresh")
        rdata = refresh_res.json()
        assert rdata["announce_configured"] is True
        assert rdata["announce_valid"] is True
        assert rdata["announce_invalid_reason"] == ""
        assert valid_url not in json.dumps(rdata)
        assert "secret_token" not in json.dumps(rdata)
    finally:
        main_mod.settings.empornium_announce_url = orig_url
        clear_cache()



