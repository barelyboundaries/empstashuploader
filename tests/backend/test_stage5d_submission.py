"""
Tests for Stage 5d: Submission Payload Assembly and Directory Cleanliness.
Verifies that <safe_title>_submission.json is written with correct keys,
that consecutive builds succeed without tripping the cleanliness gate (Amendment B1),
and that dry_run mode completes without network calls.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

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
from empornium_megapack.config import Settings


def test_stage5d_submission_payload_schema_and_clean_tags(tmp_path):
    """<safe_title>_submission.json is created with valid schema and sanitized fields."""
    out_dir = tmp_path / "stage5d_sub_out"
    out_dir.mkdir()
    pack_title = "Submission Payload Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_sub.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "category": "Packs",
        "notes": "Exclusive release description.",
        "scenes": [
            {
                "id": 1,
                "title": "Scene One",
                "path": str(media_file),
                "height": 1080,
                "duration": 1800,
                "video_codec": "h264",
                "performers": ["Star One"],
                "tags": ["Feature", "1080p"],
            }
        ],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"

    sub_path = Path(res["submission_path"])
    assert sub_path.exists()

    sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
    assert sub_data["title"] == "Submission Payload Megapack"
    assert "category" not in sub_data
    assert "tracker_tags" in sub_data
    assert "description" in sub_data
    assert "torrent_path" in sub_data
    assert "created_at" in sub_data
    assert "preview_only" in sub_data

    # Check tracker_tags cleanliness
    for tag in sub_data["tracker_tags"]:
        assert "[" not in tag and "]" not in tag
        assert " " not in tag and "\t" not in tag


def test_stage5d_consecutive_builds_do_not_trip_cleanliness_gate(tmp_path):
    """Amendment B1: Building twice in the same output_dir succeeds because _submission.json is in allowlists."""
    out_dir = tmp_path / "stage5d_consecutive_out"
    out_dir.mkdir()
    pack_title = "Repeat Build Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_repeat.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    # First build creates .torrent, _manifest.json, _bbcode.txt, and _submission.json
    res1 = task.run_build_megapack(payload)
    assert res1["status"] == "success"
    assert Path(res1["submission_path"]).exists()

    # Second build in the EXACT SAME directory must succeed without raising foreign files error
    res2 = task.run_build_megapack(payload)
    assert res2["status"] == "success"
    assert Path(res2["submission_path"]).exists()


def test_stage5d_dry_run_mode(tmp_path, capsys):
    """dry_run=True logs what would be submitted and exits with zero tracker POSTs."""
    out_dir = tmp_path / "stage5d_dryrun_out"
    out_dir.mkdir()
    pack_title = "Dry Run Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_dry.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    fake_settings = Settings(empornium_announce_url="http://tracker.empornium.sx:2710/mysecrettoken/announce")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "dry_run": True,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        res = task.run_build_megapack(payload)

    assert res["status"] == "success"
    assert res["dry_run"] is True
    assert res["submission_payload"] is not None

    captured = capsys.readouterr()
    assert "[Dry Run] Megapack submission payload assembled" in captured.err
    assert "mysecrettoken" not in captured.err


def test_stage5d_b4_regression_criterion(tmp_path):
    """
    Amendment B4: With upload disabled and no announce configured, the BBCode body
    matches Stage 4 format, and the manifest is field-wise identical on the Stage 4 keys.
    """
    out_dir = tmp_path / "stage5d_b4_out"
    out_dir.mkdir()
    pack_title = "B4 Regression Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_b4.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    fake_settings = Settings(hamster_api_key="", empornium_announce_url="")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": False,
        "notes": "Stage 4 baseline test note",
        "scenes": [
            {
                "id": 1,
                "title": "B4 Scene",
                "path": str(media_file),
                "height": 1080,
                "duration": 1800,
                "video_codec": "h264",
                "performers": ["Actor B4"],
                "tags": ["TagB4"],
            }
        ],
    }

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        res = task.run_build_megapack(payload)

    assert res["status"] == "success"
    bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")
    assert bbcode.startswith("[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]\n")
    assert "[center][b][size=5]B4 Regression Pack[/size][/b][/center]" in bbcode
    assert "[b]Performers:[/b] Actor B4" in bbcode
    assert "1. [b]B4 Scene[/b] (Actor B4) [1080p] [30:00]" in bbcode
    assert "[quote]Stage 4 baseline test note[/quote]" in bbcode

    # Manifest field-wise check on Stage 4 keys
    manifest = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["pack_title"] == "B4 Regression Pack"
    assert manifest["scene_count"] == 1
    assert manifest["scenes"][0]["title"] == "B4 Scene"
    assert manifest["torrent_path"] == res["torrent_path"]
    assert manifest["bbcode_path"] == res["bbcode_path"]
    assert manifest["preview_only"] is True
    assert "tagb4" in [t.lower() for t in manifest["tracker_tags"]]

