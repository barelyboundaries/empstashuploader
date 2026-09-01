import json
import os
import zipfile

import pytest
import torf

from empornium_megapack.build import (
    BuildError,
    CONTACT_SHEETS_DIR,
    _same_volume,
    make_bundle,
    payload_size,
    sanitize_name,
    stage_payload,
    unique_names,
)
from empornium_megapack.images import ContactSheetError
from empornium_megapack.metadata import ImagePlaceholderError, finalize_description
from empornium_megapack.models import BuildRequest, PackMetaInput, ReviewRequest, SceneMetaInput
from empornium_megapack.review import PackService
from empornium_megapack.torrents import piece_size_for, TorrentError
from test_images import _big_jpeg, fake_upload, no_backoff, settings_with, stub_vcsi, vcstub
from test_review import FakeStash, make_file, make_scene

ANNOUNCE = "https://tracker.empornium.me/announce.php?passkey=0123456789abcdef0123456789abcdef"


def _video(tmp_path, name, size=64):
    path = tmp_path / name
    path.write_bytes(b"\x00" * size)
    return str(path)


@pytest.fixture
def build_settings(tmp_path):
    base = dict(
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        bundle_after_build=True,
    )
    return settings_with(tmp_path, **base)


def test_sanitize_name_reserved_and_invalid():
    assert sanitize_name("CON") == "_CON"
    assert sanitize_name("lpt1") == "_lpt1"
    assert sanitize_name('a<b>c:"d"e|f?g*h') == "a_b_c__d_e_f_g_h"
    assert sanitize_name("  spaced  name  ") == "spaced name"
    assert sanitize_name("") == "Untitled"
    assert sanitize_name("  . ") == "Untitled"


def test_sanitize_name_trims_preserving_extension():
    name = "x" * 200 + ".mp4"
    cleaned = sanitize_name(name)
    assert len(cleaned) <= 120
    assert cleaned.endswith(".mp4")


def test_unique_names_case_insensitive():
    assert unique_names(["a", "A"]) == ["a", "A (2)"]
    assert unique_names(["a", "a", "a"]) == ["a", "a (2)", "a (3)"]


# --- stage_payload --------------------------------------------------------------


def _staged(tmp_path, build_settings, href, download_calls=None):
    root = build_settings.staging_dir / "packs" / "abc123"
    scenes = [
        {"scene_id": "1", "title": "One", "source_path": href[0], "sheet_path": href[1], "fetch_mode": "copy", "expected_size": None},
        {"scene_id": "2", "title": "One", "source_path": href[2], "sheet_path": href[3], "fetch_mode": "copy", "expected_size": None},
    ]
    return stage_payload(root, "My Pack", scenes, build_settings, http_download=download_calls)


def test_stage_payload_copies_sheets_and_dedupes_names(tmp_path, build_settings):
    sheet1 = tmp_path / "s1.jpg"
    _big_jpeg(sheet1)
    sheet2 = tmp_path / "s2.jpg"
    _big_jpeg(sheet2)
    href = (_video(tmp_path, "a.mp4"), str(sheet1), _video(tmp_path, "b.mp4"), str(sheet2))
    staged = _staged(tmp_path, build_settings, href)

    assert [s.video_name for s in staged] == ["One.mp4", "One.mp4 (2)"]
    assert [s.sheet_name for s in staged] == ["One.jpg", "One.jpg (2)"]
    root = build_settings.staging_dir / "packs" / "abc123" / "My Pack"
    assert (root / "One.mp4").is_file()
    assert (root / CONTACT_SHEETS_DIR / "One.jpg (2)").is_file()
    assert os.path.samefile(staged[0].staged_path, href[0])
    assert staged[0].linked is True  # same tmp volume -> hardlink


def test_stage_payload_copies_when_volume_differs(tmp_path, build_settings, monkeypatch):
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    monkeypatch.setattr("empornium_megapack.build._same_volume", lambda source, staging: False)
    staged = _staged(tmp_path, build_settings, (_video(tmp_path, "a.mp4"), str(sheet), _video(tmp_path, "b.mp4"), str(sheet)))
    assert staged[0].linked is False
    assert not os.path.samefile(staged[0].staged_path, tmp_path / "a.mp4")


def test_stage_payload_untitled_scene_no_double_extension(tmp_path, build_settings):
    root = build_settings.staging_dir / "packs" / "abc123"
    video = _video(tmp_path, "clip.mp4")
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    scenes = [
        {"scene_id": "1", "title": "clip.mp4", "source_path": str(video), "sheet_path": str(sheet), "fetch_mode": "copy", "expected_size": None},
    ]
    staged = stage_payload(root, "My Pack", scenes, build_settings)
    assert staged[0].video_name == "clip.mp4"
    assert staged[0].sheet_name == "clip.jpg"
    assert (root / "My Pack" / "clip.mp4").is_file()
    assert (root / "My Pack" / CONTACT_SHEETS_DIR / "clip.jpg").is_file()


def test_stage_payload_download_mode(tmp_path, build_settings):
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    calls = []

    def fake_download(scene_id, dest, expected):
        calls.append((scene_id, dest.name, expected))
        dest.write_bytes(b"\x00" * (expected or 10))

    states = [
        {"scene_id": "9", "title": "Remote", "source_path": None, "sheet_path": str(sheet), "fetch_mode": "download", "expected_size": 42},
    ]
    root = build_settings.staging_dir / "packs" / "abc123"
    staged = stage_payload(root, "My Pack", states, build_settings, http_download=fake_download)
    assert calls == [("9", "Remote.mp4", 42)]
    assert staged[0].linked is False
    assert (root / "My Pack" / "Remote.mp4").stat().st_size == 42


def test_stage_payload_download_needs_http(tmp_path, build_settings):
    states = [{"scene_id": "9", "title": "Remote", "source_path": None, "sheet_path": str(tmp_path / "nope.jpg"), "fetch_mode": "download", "expected_size": 1}]
    with pytest.raises(BuildError, match="HTTP download"):
        stage_payload(build_settings.staging_dir / "packs" / "x", "My Pack", states, build_settings)


# --- payload_size / make_bundle ---------------------------------------------------


def test_payload_size_and_bundle(tmp_path, build_settings):
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    video = _video(tmp_path, "a.mp4", size=5)
    df = tmp_path / "packs" / "abc123"
    stage_payload(df, "My Pack", [
        {"scene_id": "1", "title": "One", "source_path": video, "sheet_path": str(sheet), "fetch_mode": "copy", "expected_size": None},
    ], build_settings)
    video_size = (df / "My Pack" / "One.mp4").stat().st_size
    sheet_size = (df / "My Pack" / CONTACT_SHEETS_DIR / "One.jpg").stat().st_size
    assert payload_size(df) == video_size + sheet_size

    bundle = tmp_path / "pack.bundle.zip"
    make_bundle(df, bundle)
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
    assert any(n.endswith("My Pack/One.mp4") for n in names)
    assert any(CONTACT_SHEETS_DIR in n for n in names)


# --- finalize_description ------------------------------------------------------------


def test_finalize_description_replaces_in_order():
    assert finalize_description("[img]{scene-image-1}[/img]", ["u1"]) == "[img]u1[/img]"
    assert finalize_description("{scene-image-1} {scene-image-2}", ["u1", "u2"]) == "u1 u2"


def test_finalize_description_leftover_placeholder_raises():
    with pytest.raises(ImagePlaceholderError, match="scene-image-2"):
        finalize_description("{scene-image-1} {scene-image-2}", ["u1"])


def test_finalize_description_out_of_range_raises():
    with pytest.raises(ImagePlaceholderError, match="scene-image-3"):
        finalize_description("{scene-image-3}", ["u1", "u2"])


def test_finalize_description_empty_urls_raises():
    with pytest.raises(ImagePlaceholderError, match="scene-image-1"):
        finalize_description("{scene-image-1}", [])


def test_finalize_description_no_placeholders_raises():
    with pytest.raises(ImagePlaceholderError, match="no image placeholders"):
        finalize_description("[img]https://x/y.jpg[/img]", ["u1"])


# --- PackService.build ----------------------------------------------------------------


def _build_scenes(tmp_path, download_size=None):
    a = _video(tmp_path, "a.mp4", size=4096)
    s1 = tmp_path / "s1.jpg"
    _big_jpeg(s1)
    scenes = {
        "1": make_scene("1", [make_file("f1", a, size=4096)], title="First Title"),
    }
    if download_size is not None:
        missing = str(tmp_path / "missing.mp4")
        scenes["2"] = make_scene("2", [make_file("f2", missing, size=download_size)], title="Second Title")
    return scenes, s1


class FakeStream:
    def __init__(self, size):
        self._size = size

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield b"\x00" * self._size


class FakeHttp:
    def __init__(self, expected):
        self.expected = expected
        self.calls = []

    def stream(self, method, url, headers=None):
        self.calls.append(url)
        return FakeStream(self.expected)


def test_pack_build_end_to_end(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes))
    resp = svc.build(BuildRequest(scene_ids=["1"]))

    assert resp.errors == []
    assert len(resp.pack_id) == 10
    assert "{scene-image-" not in resp.description
    assert "https://img.example/x.jpg" in resp.description
    assert "passkey" not in resp.description

    root = build_settings.staging_dir / "packs" / resp.pack_id
    payload_dir = root / resp.title
    assert (payload_dir / "First Title.mp4").is_file()
    assert (payload_dir / CONTACT_SHEETS_DIR / "First Title.jpg").is_file()

    torrent_bytes = (build_settings.output_dir / f"{resp.pack_id}.torrent").read_bytes()
    assert b"manifest" not in torrent_bytes
    torrent = torf.Torrent.read(build_settings.output_dir / f"{resp.pack_id}.torrent")
    assert torrent.private is True
    assert torrent.trackers == [[ANNOUNCE]]
    assert torrent.piece_size == piece_size_for(resp.total_bytes)

    manifest = json.loads((build_settings.output_dir / f"{resp.pack_id}.manifest.json").read_text())
    assert manifest["infohash"] == resp.infohash
    assert manifest["scenes"][0]["sheet_url"] == "https://img.example/x.jpg"
    assert manifest["scenes"][0]["linked"] in (True, False)
    assert manifest["scenes"][0]["video_name"] == "First Title.mp4"
    assert "announce" not in json.dumps(manifest)
    assert resp.total_bytes == manifest["total_bytes"]

    desc_path = build_settings.output_dir / f"{resp.pack_id}.description.txt"
    assert desc_path.is_file()
    assert (build_settings.output_dir / f"{resp.pack_id}.bundle.zip").is_file()
    assert resp.bundle_file == f"{resp.pack_id}.bundle.zip"
    assert resp.pack_id in svc.packs

    cleanup = svc.cleanup(resp.pack_id)
    assert cleanup.staging_removed is True
    assert not root.exists()
    assert (build_settings.output_dir / f"{resp.pack_id}.torrent").is_file()


def test_pack_build_download_and_warnings(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path, download_size=8)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes), http=FakeHttp(8))
    meta = PackMetaInput(scenes=[SceneMetaInput(scene_id="2", fetch_mode="download")])
    resp = svc.build(BuildRequest(scene_ids=["2"], meta=meta))

    assert resp.errors == []
    assert svc.http.calls == [f"{build_settings.stash_url}/scene/2/stream"]
    assert resp.scenes[0].linked is False
    assert any(w.code == "http_download" for w in resp.warnings)
    payload_dir = build_settings.staging_dir / "packs" / resp.pack_id / resp.title
    assert (payload_dir / "Second Title.mp4").stat().st_size == 8


def test_pack_build_download_size_mismatch_fails(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path, download_size=8)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes), http=FakeHttp(99))
    meta = PackMetaInput(scenes=[SceneMetaInput(scene_id="2", fetch_mode="download")])
    with pytest.raises(BuildError, match="Stream size mismatch"):
        svc.build(BuildRequest(scene_ids=["2"], meta=meta))


def test_pack_build_requires_announce(tmp_path):
    settings = settings_with(tmp_path, output_dir=tmp_path / "output", empornium_announce_url="")
    svc = PackService(settings=settings, stash=FakeStash({}))
    with pytest.raises(BuildError, match="Empornium announce"):
        svc.build(BuildRequest(scene_ids=["1"]))


def test_pack_build_rejects_bad_announce(tmp_path):
    settings = settings_with(tmp_path, output_dir=tmp_path / "output", empornium_announce_url="ftp://tracker.example/announce")
    svc = PackService(settings=settings, stash=FakeStash({}))
    with pytest.raises(TorrentError):
        svc.build(BuildRequest(scene_ids=["1"]))


def test_pack_build_unknown_scene_returns_errors(tmp_path, build_settings):
    svc = PackService(settings=build_settings, stash=FakeStash({}))
    resp = svc.build(BuildRequest(scene_ids=["42"]))
    assert resp.pack_id == ""
    assert any(e.code == "unknown_scene" for e in resp.errors)


def test_pack_build_missing_api_key_raises(tmp_path, vcstub, stub_vcsi, no_backoff):
    settings = settings_with(
        tmp_path,
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        hamster_api_key="",
    )
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    stub_vcsi(extra=["--payload", str(sheet)])
    videos = _video(tmp_path, "a.mp4")
    scenes = {"1": make_scene("1", [make_file("f1", videos)])}
    svc = PackService(settings=settings, stash=FakeStash(scenes))
    with pytest.raises(ContactSheetError, match="API key"):
        svc.build(BuildRequest(scene_ids=["1"]))
    assert len(svc.packs) == 0


def test_pack_build_rolls_back_staging_and_artifacts(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff, monkeypatch):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes))

    def boom(payload_dir, announce_url, out_path, **kwargs):
        raise BuildError("torrent generation needs 6 more MiB")

    monkeypatch.setattr("empornium_megapack.review.create_torrent", boom)
    with pytest.raises(BuildError, match="6 more MiB"):
        svc.build(BuildRequest(scene_ids=["1"]))
    assert not (build_settings.staging_dir / "packs").exists() or not any((build_settings.staging_dir / "packs").iterdir())
    assert not build_settings.output_dir.exists() or list(build_settings.output_dir.iterdir()) == []


def test_pack_build_bundle_contains_artifacts(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes))
    resp = svc.build(BuildRequest(scene_ids=["1"]))
    with zipfile.ZipFile(build_settings.output_dir / resp.bundle_file) as zf:
        names = zf.namelist()
    assert resp.bundle_file == f"{resp.pack_id}.bundle.zip"
    assert f"{resp.pack_id}.torrent" in names
    assert f"{resp.pack_id}.manifest.json" in names
    assert f"{resp.pack_id}.description.txt" in names
    assert any(n.endswith("First Title.mp4") for n in names)
    assert any("Contact Sheets" in n for n in names)


def test_cleanup_rejects_path_traversal(build_settings):
    svc = PackService(settings=build_settings, stash=FakeStash({}))
    with pytest.raises(ValueError, match="invalid pack id"):
        svc.cleanup("../..")


def test_cleanup_unknown_but_valid_id_is_idempotent(build_settings):
    svc = PackService(settings=build_settings, stash=FakeStash({}))
    assert svc.cleanup("0123456789").staging_removed is False


def test_pack_artifact_gated_by_registry(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes))
    resp = svc.build(BuildRequest(scene_ids=["1"]))
    assert svc.pack_artifact(resp.pack_id, "torrent").is_file()
    assert svc.pack_artifact(resp.pack_id, "bundle").is_file()
    with pytest.raises(FileNotFoundError):
        svc.pack_artifact(resp.pack_id, "nope")
    with pytest.raises(FileNotFoundError):
        svc.pack_artifact("0123456789", "torrent")
    with pytest.raises(ValueError):
        svc.pack_artifact("../../x", "torrent")


def test_build_multi_file_download_rejected(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    a = _video(tmp_path, "a.mp4", size=4096)
    b = _video(tmp_path, "b.mkv", size=4096)
    scenes = {"1": make_scene("1", [make_file("f1", a), make_file("f2", b)], title="Multi")}
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    stub_vcsi(extra=["--payload", str(sheet)])
    svc = PackService(settings=build_settings, stash=FakeStash(scenes), http=FakeHttp(8))
    meta = PackMetaInput(scenes=[SceneMetaInput(scene_id="1", fetch_mode="download")])
    resp = svc.build(BuildRequest(scene_ids=["1"], meta=meta))
    assert any(e.code == "download_multi_file" for e in resp.errors)
    assert len(svc.packs) == 0


def test_pack_registry_persists_across_restart(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    scenes, sheet = _build_scenes(tmp_path)
    stub_vcsi(extra=["--payload", str(sheet)])
    first = PackService(settings=build_settings, stash=FakeStash(scenes))
    resp = first.build(BuildRequest(scene_ids=["1"]))
    assert first.packs[resp.pack_id]["torrent"]

    restarted = PackService(settings=build_settings, stash=FakeStash({}))
    assert resp.pack_id in restarted.packs
    assert restarted.pack_artifact(resp.pack_id, "torrent").is_file()
    assert restarted.pack_artifact(resp.pack_id, "manifest").is_file()
    assert restarted.pack_artifact(resp.pack_id, "bundle").is_file()


def test_pack_registry_rejects_malformed_index(build_settings):
    index = build_settings.output_dir / "packs.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"ok": {"torrent": "x.torrent"}, "..%2F..": {"torrent": "y.torrent"}, "0123456789": {"torrent": "z.torrent"}}))
    svc = PackService(settings=build_settings, stash=FakeStash({}))
    assert "..%2F.." not in svc.packs
    assert "ok" not in svc.packs
    assert svc.packs["0123456789"]["torrent"] == "z.torrent"


def test_build_mapped_oshash_mismatch_is_inaccessible(tmp_path, build_settings):
    mapped = tmp_path / "wrong.mp4"
    mapped.write_bytes(b"\x00" * 5)
    settings = settings_with(
        tmp_path,
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        path_mappings=[["/media", str(tmp_path)]],
    )
    scenes = {"1": make_scene("1", [make_file("f1", "/media/wrong.mp4", size=4096)], title="Mismatch")}
    scenes["1"]["files"][0]["oshash"] = "0" * 32
    svc = PackService(settings=settings, stash=FakeStash(scenes))
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    check = next(f for s in resp.scenes for f in s.files)
    assert check.exists is True
    assert check.accessible is False


def test_build_mapped_oshash_matches_is_usable(tmp_path):
    real = _video(tmp_path, "right.mp4", size=4096)
    settings = settings_with(
        tmp_path,
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        path_mappings=[["/media", str(tmp_path)]],
    )
    from empornium_megapack.paths import oshash_file

    osh = oshash_file(real)
    scenes = {"1": make_scene("1", [make_file("f1", "/media/right.mp4", size=4096)], title="Mapped")}
    scenes["1"]["files"][0]["oshash"] = osh
    svc = PackService(settings=settings, stash=FakeStash(scenes))
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    check = next(f for s in resp.scenes for f in s.files)
    assert check.accessible is True


def test_build_maps_docker_paths(tmp_path, build_settings, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    local_video = _video(tmp_path, "local.mp4", size=4096)
    sheet = tmp_path / "s.jpg"
    _big_jpeg(sheet)
    stub_vcsi(extra=["--payload", str(sheet)])
    settings = settings_with(
        tmp_path,
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        path_mappings=[["/media", str(tmp_path)]],
    )
    scenes = {"1": make_scene("1", [make_file("f1", "/media/local.mp4", size=4096)], title="Docker Scene")}
    svc = PackService(settings=settings, stash=FakeStash(scenes))
    resp = svc.build(BuildRequest(scene_ids=["1"]))
    assert resp.errors == []
    payload_dir = settings.staging_dir / "packs" / resp.pack_id / resp.title
    staged_video = payload_dir / "Docker Scene.mp4"
    assert staged_video.is_file()
    assert os.path.samefile(staged_video, local_video)


def test_build_mapped_size_mismatch_is_inaccessible(tmp_path, build_settings):
    mapped = tmp_path / "wrong.mp4"
    mapped.write_bytes(b"\x00" * 5)
    settings = settings_with(
        tmp_path,
        output_dir=tmp_path / "output",
        empornium_announce_url=ANNOUNCE,
        path_mappings=[["/media", str(tmp_path)]],
    )
    scenes = {"1": make_scene("1", [make_file("f1", "/media/wrong.mp4", size=4096)], title="Mismatch")}
    svc = PackService(settings=settings, stash=FakeStash(scenes))
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    file_review = next(f for s in resp.scenes for f in s.files)
    assert file_review.exists is True
    assert file_review.accessible is False