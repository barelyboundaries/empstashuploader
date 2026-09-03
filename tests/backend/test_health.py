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

