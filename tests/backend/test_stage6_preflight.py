"""
Tests for Stage 6 R4 (6d) & R5 (6e): Pre-Flight Checklist Verification & Artifact Validation.
Verifies torf read-back, private=True, non-empty pieces, source tag, on-disk file existence,
root name comparison, and upload link affordances.
"""

import json
import os
import sys
from pathlib import Path
import pytest
import torf

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
PLUGIN_DIR = PROJECT_ROOT / "plugin"

if str(BACKEND_DIR) in sys.path:
    sys.path.remove(str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR))

if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import task
from empornium_megapack.build import verify_preflight_checklist


def test_stage6_preflight_all_checks_pass_on_valid_build(tmp_path):
    """Happy path: valid torrent, remote images, clean tags, on-disk files -> ready is True."""
    out_dir = tmp_path / "valid_pack_dir"
    out_dir.mkdir()

    # Create dummy media files
    f1 = out_dir / "scene_01.mp4"
    f2 = out_dir / "scene_02.mkv"
    f1.write_bytes(b"A" * 1024 * 1024)
    f2.write_bytes(b"B" * 1024 * 1024)

    # Create valid torrent using torf
    torrent = torf.Torrent(
        path=str(out_dir),
        trackers=[["http://tracker.empornium.sx:2710/passkey1234/announce"]],
        private=True,
        source="Emp",
    )
    torrent.generate()
    tor_path = out_dir / "Valid Pack.torrent"
    torrent.write(str(tor_path))

    sub_data = {
        "title": "Valid Pack",
        "tracker_tags": ["tag1", "tag2"],
        "image_urls": [
            "https://hamsterimg.net/images/2026/08/23/sheet1.jpg",
            "https://hamsterimg.net/images/2026/08/23/sheet2.jpg",
        ],
        "preview_only": False,
        "torrent_path": str(tor_path),
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=out_dir,
        pack_title="valid_pack_dir",
        submission_data=sub_data,
    )

    assert res["ready"] is True
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["images_remote"]["passed"] is True
    assert checks_by_id["tracker_tags"]["passed"] is True
    assert checks_by_id["torrent_valid"]["passed"] is True
    assert checks_by_id["payload_files"]["passed"] is True
    assert checks_by_id["category"]["is_info"] is True


def test_stage6_preflight_fails_when_preview_only(tmp_path):
    """Pre-flight fails when preview_only is True or image URLs contain local file:///."""
    out_dir = tmp_path / "preview_pack"
    out_dir.mkdir()
    f1 = out_dir / "s1.mp4"
    f1.write_bytes(b"A" * 1024)

    torrent = torf.Torrent(path=str(out_dir), trackers=[["http://tracker.empornium.sx/announce"]], private=True, source="Emp")
    torrent.generate()
    tor_path = out_dir / "pack.torrent"
    torrent.write(str(tor_path))

    sub_data = {
        "title": "Preview Pack",
        "tracker_tags": ["tag1"],
        "image_urls": ["file:///C:/Media/preview1.jpg"],
        "preview_only": True,
        "torrent_path": str(tor_path),
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=out_dir,
        pack_title="preview_pack",
        submission_data=sub_data,
    )

    assert res["ready"] is False
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["images_remote"]["passed"] is False
    assert "local file:///" in checks_by_id["images_remote"]["detail"]


def test_stage6_preflight_fails_when_torrent_is_hollow_or_missing_source(tmp_path):
    """Pre-flight reads back torrent artifact and catches missing source or private=False."""
    out_dir = tmp_path / "bad_torrent_pack"
    out_dir.mkdir()
    f1 = out_dir / "s1.mp4"
    f1.write_bytes(b"A" * 1024)

    # Torrent without private or source
    torrent = torf.Torrent(path=str(out_dir), trackers=[["http://tracker.empornium.sx/announce"]], private=False)
    torrent.generate()
    tor_path = out_dir / "bad.torrent"
    torrent.write(str(tor_path))

    sub_data = {
        "title": "Bad Torrent Pack",
        "tracker_tags": ["tag1"],
        "image_urls": ["https://hamsterimg.net/img.jpg"],
        "preview_only": False,
        "torrent_path": str(tor_path),
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=out_dir,
        pack_title="bad_torrent_pack",
        submission_data=sub_data,
    )

    assert res["ready"] is False
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["torrent_valid"]["passed"] is False
    assert "private flag is False" in checks_by_id["torrent_valid"]["detail"] or "source tag missing" in checks_by_id["torrent_valid"]["detail"]


def test_stage6_preflight_warns_when_root_name_differs_from_pack_title(tmp_path):
    """Pre-flight warns (without blocking ready=True) when torrent root name differs from pack title."""
    out_dir = tmp_path / "Folder_Name"
    out_dir.mkdir()
    f1 = out_dir / "s1.mp4"
    f1.write_bytes(b"A" * 1024)

    torrent = torf.Torrent(path=str(out_dir), trackers=[["http://tracker.empornium.sx/announce"]], private=True, source="Emp")
    torrent.generate()
    tor_path = out_dir / "pack.torrent"
    torrent.write(str(tor_path))

    sub_data = {
        "title": "Different Pack Title",
        "tracker_tags": ["tag1"],
        "image_urls": ["https://hamsterimg.net/img.jpg"],
        "preview_only": False,
        "torrent_path": str(tor_path),
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=out_dir,
        pack_title="Different Pack Title",
        submission_data=sub_data,
    )

    # Root name difference is a warning, not a hard block
    assert res["ready"] is True
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["root_name"]["is_warning"] is True
    assert "differs from pack title" in checks_by_id["root_name"]["detail"]


def test_stage6_run_build_megapack_includes_preflight_artifact_results(tmp_path):
    """run_build_megapack automatically executes preflight and attaches it to manifest and result."""
    out_dir = tmp_path / "live_build_preflight"
    out_dir.mkdir()
    f1 = out_dir / "scene_live.mp4"
    f1.write_bytes(b"A" * 1024)

    payload = {
        "pack_title": "live_build_preflight",
        "output_dir": str(out_dir),
        "tags": ["TagA", "TagB"],
        "scenes": [{"id": 1, "path": str(f1), "title": "Scene 1"}],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    assert "preflight" in res
    assert "ready" in res

    # Check manifest file has preflight
    man_path = Path(res["manifest_path"])
    assert man_path.exists()
    man_data = json.loads(man_path.read_text(encoding="utf-8"))
    assert "preflight" in man_data
    assert isinstance(man_data["preflight"]["checks"], list)
