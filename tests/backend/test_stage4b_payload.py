"""
Tests for Stage 4b: Widened Scene Payload.
Verifies that expanded scene attributes (height, width, duration, date, studio)
are preserved in the emitted _manifest.json and that missing/null fields are handled safely.
"""

import json
import os
import sys
from pathlib import Path
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
from empornium_megapack.metadata import resolution_for, format_duration


def test_stage4b_widened_scene_payload_in_manifest(tmp_path):
    """Manifest JSON accurately records height, width, duration, date, and studio for scenes."""
    out_dir = tmp_path / "stage4b_out"
    out_dir.mkdir()
    pack_title = "Widened Payload Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_full_meta.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    scene_record = {
        "id": 101,
        "title": "Full Metadata Scene",
        "path": str(media_file),
        "size": 2048,
        "height": 1080,
        "width": 1920,
        "duration": 3665.5,
        "video_codec": "h264",
        "date": "2026-05-12",
        "studio": "Studio Empornium Megapack Builder",
        "performers": ["Alice Stone"],
        "tags": ["1080p", "Feature"],
    }

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [scene_record],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"

    manifest_path = Path(res["manifest_path"])
    assert manifest_path.exists()

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_data["scene_count"] == 1
    assert len(manifest_data["scenes"]) == 1

    saved_scene = manifest_data["scenes"][0]
    assert saved_scene["id"] == 101
    assert saved_scene["height"] == 1080
    assert saved_scene["width"] == 1920
    assert saved_scene["duration"] == 3665.5
    assert saved_scene["video_codec"] == "h264"
    assert saved_scene["date"] == "2026-05-12"
    assert saved_scene["studio"] == "Studio Empornium Megapack Builder"

    # Assert tracker_tags is populated for Stage 5 upload engine
    assert "tracker_tags" in manifest_data
    assert "tracker_tags" in res
    assert "1080p" in manifest_data["tracker_tags"]
    assert "h264" in manifest_data["tracker_tags"]
    assert "alice.stone" in manifest_data["tracker_tags"]


def test_stage4b_missing_optional_attributes_graceful(tmp_path):
    """Scenes missing height, duration, date, or studio still build successfully without error."""
    out_dir = tmp_path / "stage4b_missing_out"
    out_dir.mkdir()
    pack_title = "Sparse Payload Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_sparse_meta.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    sparse_scene = {
        "id": 202,
        "title": "Sparse Scene",
        "path": str(media_file),
        "height": None,
        "width": None,
        "duration": None,
        "date": None,
        "studio": None,
    }

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [sparse_scene],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"

    manifest_data = json.loads(Path(res["manifest_path"]).read_text(encoding="utf-8"))
    saved_scene = manifest_data["scenes"][0]
    assert saved_scene["height"] is None
    assert saved_scene["duration"] is None
    assert saved_scene["studio"] is None

    # Test metadata helper safety
    assert resolution_for(saved_scene["height"]) == ""
    assert format_duration(saved_scene["duration"]) == ""
