import ctypes
import os
import sys
from ctypes import wintypes

from deepseek_megapack.config import Settings
from deepseek_megapack.models import ApplyRequest, ReviewRequest
from deepseek_megapack.review import (
    ByHandleFileInfo,
    PackService,
    _close_handle,
    _create_file_w,
    _get_file_information_by_handle,
    _needs_copy,
    _normname,
    _os_creation_time,
)


def make_scene(sid, files, title="", created_at="2026-01-01T00:00:00-06:00", studio=None, performers=(), tags=(), date=None):
    return {
        "id": sid,
        "title": title,
        "date": date,
        "rating100": 0,
        "created_at": created_at,
        "urls": [],
        "studio": {"name": studio} if studio else None,
        "performers": [{"name": p} for p in performers],
        "tags": [{"name": t} if isinstance(t, str) else t for t in tags],
        "files": files,
    }


def make_file(fid, path, mod_time="2026-01-01T12:00:00-06:00", created_at="2026-01-01T00:00:00-06:00", size=1000, video_codec="hevc"):
    return {
        "id": fid,
        "path": path,
        "basename": os.path.basename(path),
        "mod_time": mod_time,
        "created_at": created_at,
        "size": size,
        "width": 1920,
        "height": 1080,
        "duration": 100.0,
        "video_codec": video_codec,
    }


def make_scene_review(scene_dict) -> "SceneReview":
    from deepseek_megapack.models import SceneReview

    return SceneReview(
        scene_id=scene_dict["id"],
        title=scene_dict.get("title") or "",
        date=scene_dict.get("date"),
        studio=(scene_dict.get("studio") or {}).get("name"),
        performers=[p["name"] for p in scene_dict.get("performers", [])],
        tags=[t["name"] for t in scene_dict.get("tags", [])],
        created_at=scene_dict.get("created_at") or "",
    )


class FakeStash:
    def __init__(self, scenes):
        self.scenes = scenes

    def fetch_scenes(self, ids):
        return {sid: self.scenes.get(sid) for sid in ids}


def service(tmp_path, scenes, policy="mod_time", ascending=True):
    settings = Settings(
        stash_url="http://unused",
        staging_dir=tmp_path / "staging",
        file_time_policy=policy,
        file_time_ascending=ascending,
    )
    return PackService(settings=settings, stash=FakeStash(scenes))


def touch(path: str, mtime: float):
    with open(path, "wb") as fh:
        fh.write(b"x")
    os.utime(path, (mtime, mtime))
    return path


def test_order_by_mod_time_ascending(tmp_path):
    old = touch(str(tmp_path / "old.mp4"), 1_700_000_000)
    new = touch(str(tmp_path / "new.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", old, mod_time="2023-11-14T22:13:20-06:00")]),
            "2": make_scene("2", [make_file("f2", new, mod_time="2023-11-24T22:13:20-06:00")]),
        },
    )
    resp = svc.review(ReviewRequest(scene_ids=["1", "2"]))
    assert [s.scene_id for s in resp.scenes] == ["1", "2"]
    assert resp.policy.ascending is True


def test_order_descending(tmp_path):
    old = touch(str(tmp_path / "old.mp4"), 1_700_000_000)
    new = touch(str(tmp_path / "new.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", old, mod_time="2023-11-14T22:13:20-06:00")]),
            "2": make_scene("2", [make_file("f2", new, mod_time="2023-11-24T22:13:20-06:00")]),
        },
        ascending=False,
    )
    resp = svc.review(ReviewRequest(scene_ids=["1", "2"]))
    assert [s.scene_id for s in resp.scenes] == ["2", "1"]


def test_tie_break_by_database_time(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    f2 = touch(str(tmp_path / "b.mp4"), 1_700_000_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", f1, mod_time="2023-11-14T22:13:20-06:00", created_at="2023-01-01T00:00:00-06:00")]),
            "2": make_scene("2", [make_file("f2", f2, mod_time="2023-11-14T22:13:20-06:00", created_at="2022-01-01T00:00:00-06:00")]),
        },
    )
    resp = svc.review(ReviewRequest(scene_ids=["1", "2"]))
    assert [s.scene_id for s in resp.scenes] == ["2", "1"]


def test_creation_policy_uses_os_creation_on_windows(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f)])}, policy="creation")
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    if sys.platform == "win32":
        assert resp.scenes[0].files[0].time_source == "creation"
    else:
        assert resp.scenes[0].files[0].time_source in ("creation", "creation_unavailable")


def test_os_creation_time_win32_api(tmp_path):
    f = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    t = _os_creation_time(str(f))
    if sys.platform == "win32":
        assert t is not None
        assert abs(t - os.stat(f).st_ctime) < 2.0
        assert t >= _os_creation_time(__file__) or _os_creation_time(__file__) is None
    else:
        assert t is None
    assert _os_creation_time(str(tmp_path / "missing.mp4")) is None


def test_os_creation_time_invalid_handle_fails_safely(tmp_path):
    if sys.platform != "win32":
        import pytest

        pytest.skip("Windows-only kernel32 API")
    assert _os_creation_time(str(tmp_path / "nope.mp4")) is None
    info = ByHandleFileInfo()
    assert _get_file_information_by_handle(0, ctypes.byref(info)) == 0
    assert _close_handle(0) == 0


def test_win32_prototypes_declared():
    if sys.platform != "win32":
        import pytest

        pytest.skip("Windows-only kernel32 API")
    assert _create_file_w.restype is wintypes.HANDLE
    assert _get_file_information_by_handle.restype is wintypes.BOOL
    assert _close_handle.restype is wintypes.BOOL
    assert len(_create_file_w.argtypes) == 7
    assert _get_file_information_by_handle.argtypes[0] is wintypes.HANDLE


def test_unknown_scene_id_reported(tmp_path):
    svc = service(tmp_path, {})
    resp = svc.review(ReviewRequest(scene_ids=["999"]))
    assert [e.code for e in resp.errors] == ["unknown_scene"]
    assert resp.scenes == []


def test_missing_file_issue(tmp_path):
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", str(tmp_path / "gone.mp4"))])},
    )
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    assert resp.scenes[0].issues[0].code == "file_missing"
    assert resp.errors == []


def test_duplicate_basename_issue(tmp_path):
    f1 = touch(str(tmp_path / "same.mp4"), 1_700_000_000)
    sub = tmp_path / "sub"
    sub.mkdir()
    f2 = touch(str(sub / "same.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", f1)]),
            "2": make_scene("2", [make_file("f2", f2)]),
        },
    )
    resp = svc.review(ReviewRequest(scene_ids=["1", "2"]))
    codes = {(i.scene_id, i.code) for s in resp.scenes for i in s.issues}
    assert ("1", "duplicate_name") in codes
    assert ("2", "duplicate_name") in codes


def test_duplicate_ignores_non_primary_files(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    f2 = touch(str(tmp_path / "dup.mp4"), 1_700_100_000)
    sub = tmp_path / "sub"
    sub.mkdir()
    f3 = touch(str(sub / "dup.mp4"), 1_700_200_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", f1, mod_time="2023-11-14T22:13:20-06:00"), make_file("f2", f2, mod_time="2023-11-20T22:13:20-06:00")]),
            "2": make_scene("2", [make_file("f3", f3)]),
        },
    )
    resp = svc.review(ReviewRequest(scene_ids=["1", "2"]))
    codes = {(i.scene_id, i.code) for s in resp.scenes for i in s.issues}
    assert ("1", "duplicate_name") not in codes
    assert ("2", "duplicate_name") not in codes

    blocked = svc.apply(ApplyRequest(scene_ids=["1", "2"], file_choices={"1": "f2"}))
    assert [e.code for e in blocked.errors] == ["duplicate_name", "duplicate_name"]
    assert blocked.pack == []


def test_no_files_issue(tmp_path):
    svc = service(tmp_path, {"1": make_scene("1", [])})
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    assert resp.scenes[0].issues[0].code == "no_files"


def test_multi_file_needs_choice_and_apply(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    f2 = touch(str(tmp_path / "b.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {"1": make_scene("1", [make_file("f1", f1), make_file("f2", f2)])},
    )
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    assert resp.scenes[0].needs_choice is True
    assert resp.scenes[0].provisional_file_id == "f1"

    blocked = svc.apply(ApplyRequest(scene_ids=["1"], file_choices={}))
    assert [e.code for e in blocked.errors] == ["needs_choice"]
    assert blocked.pack == []

    chosen = svc.apply(ApplyRequest(scene_ids=["1"], file_choices={"1": "f2"}))
    assert chosen.errors == []
    assert chosen.pack[0].primary_file.file_id == "f2"


def test_apply_respects_request_policy_override(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f1)])}, policy="mod_time")
    resp = svc.apply(
        ApplyRequest(
            scene_ids=["1"],
            file_time_policy="creation",
            file_time_ascending=False,
        )
    )
    assert resp.policy.name == "creation"
    assert resp.policy.ascending is False


def test_apply_defaults_to_config_policy(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f1)])}, policy="mod_time", ascending=False)
    resp = svc.apply(ApplyRequest(scene_ids=["1"]))
    assert resp.policy.name == "mod_time"
    assert resp.policy.ascending is False


def test_apply_respects_request_order(tmp_path):
    old = touch(str(tmp_path / "old.mp4"), 1_700_000_000)
    new = touch(str(tmp_path / "new.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", old, mod_time="2023-11-14T22:13:20-06:00")]),
            "2": make_scene("2", [make_file("f2", new, mod_time="2023-11-24T22:13:20-06:00")]),
        },
    )
    resp = svc.apply(ApplyRequest(scene_ids=["2", "1"]))
    assert [r.scene_id for r in resp.pack] == ["2", "1"]


def test_apply_blocks_duplicate_primaries(tmp_path):
    f1 = touch(str(tmp_path / "dup.mp4"), 1_700_000_000)
    sub = tmp_path / "sub"
    sub.mkdir()
    f2 = touch(str(sub / "dup.mp4"), 1_700_100_000)
    svc = service(
        tmp_path,
        {
            "1": make_scene("1", [make_file("f1", f1)]),
            "2": make_scene("2", [make_file("f2", f2)]),
        },
    )
    resp = svc.apply(ApplyRequest(scene_ids=["1", "2"]))
    assert [e.code for e in resp.errors] == ["duplicate_name", "duplicate_name"]
    assert resp.pack == []


def test_duplicate_request_ids_deduped(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f1)])})
    resp = svc.review(ReviewRequest(scene_ids=["1", "1"]))
    assert len(resp.scenes) == 1


def test_no_cross_volume_warning_on_same_volume(tmp_path):
    f1 = touch(str(tmp_path / "a.mp4"), 1_700_000_000)
    svc = service(tmp_path, {"1": make_scene("1", [make_file("f1", f1)])})
    resp = svc.review(ReviewRequest(scene_ids=["1"]))
    codes = [w.code for w in resp.warnings]
    assert "cross_volume_copy" not in codes


def test_needs_copy_helper():
    assert _needs_copy(1, 1) is False
    assert _needs_copy(1, 2) is True
    assert _needs_copy(None, 1) is True


def test_normname_case_insensitive():
    assert _normname("A.MP4") == _normname("a.mp4")
