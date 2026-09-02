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
    monkeypatch.setenv("EMPORNIUM_BUILD_STAMP", "0.2.0-customsha")
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["build_stamp"] == "0.2.0-customsha"
    assert body["version"] == "0.2.0"


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

