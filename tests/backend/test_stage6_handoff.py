"""
Tests for Stage 6: Handoff Quality & Manual Upload Preparation.
Verifies removal of category field (6b), tracker tags format, and submission artifact schema.
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


def test_stage6_submission_json_omits_category(tmp_path):
    """Stage 6 R2 (6b): category is removed from submission assembly and _submission.json."""
    out_dir = tmp_path / "stage6_nocat_out"
    out_dir.mkdir()
    pack_title = "No Category Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_6.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "category": "Packs",  # Even if sent in payload, must be ignored / not present in submission
        "scenes": [{"id": 1, "path": str(media_file), "title": "Scene 1"}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"

    # Verify submission.json file does not have category
    sub_path = Path(result["submission_path"])
    assert sub_path.exists()
    sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
    assert "category" not in sub_data

    # Verify return dict submission_payload does not have category
    assert "category" not in result["submission_payload"]

    # Verify essential fields remain
    assert sub_data["title"] == "No Category Pack"
    assert "tracker_tags" in sub_data
    assert "description" in sub_data
    assert "torrent_path" in sub_data
    assert "preview_only" in sub_data


def test_stage6_tracker_tags_clean_and_space_joinable(tmp_path):
    """Tracker tags in submission payload are clean strings suitable for space-joining in UI."""
    out_dir = tmp_path / "stage6_tags_out"
    out_dir.mkdir()
    pack_title = "Tags Cleanliness Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_tags.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "tags": ["Big [Tag]", "Special.Char-Tag", "Multiple Words Tag"],
        "scenes": [{"id": 1, "path": str(media_file), "tags": ["SceneTag_1"]}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"

    sub_data = json.loads(Path(result["submission_path"]).read_text(encoding="utf-8"))
    tags = sub_data["tracker_tags"]
    assert isinstance(tags, list)
    assert len(tags) > 0

    # Every tag must be lowercase and contain no spaces, brackets, or metacharacters
    for t in tags:
        assert "[" not in t and "]" not in t
        assert " " not in t
        assert t.islower() or t.replace(".", "").replace("-", "").replace("_", "").isalnum()

    # Space-joined representation must be valid
    joined = " ".join(tags)
    assert "[" not in joined and "]" not in joined


def test_stage6_empornium_site_url_setting_and_payload_resolution(tmp_path, monkeypatch):
    """Stage 6 cleanup: empornium_site_url is configurable, defaults to empty, and surfaces in submission.json."""
    from deepseek_megapack.config import Settings, get_settings

    # Verify default in Settings is empty string (no hardcoded domain)
    fresh_settings = Settings()
    assert fresh_settings.empornium_site_url == ""

    out_dir = tmp_path / "stage6_site_url_out"
    out_dir.mkdir()
    pack_title = "Site URL Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_site.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    # 1. Build with custom site_url passed in payload
    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "site_url": "https://www.empornium.sx",
        "scenes": [{"id": 1, "path": str(media_file), "title": "Scene 1"}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert result["site_url"] == "https://www.empornium.sx"

    # Verify present in submission.json
    sub_data = json.loads(Path(result["submission_path"]).read_text(encoding="utf-8"))
    assert sub_data["site_url"] == "https://www.empornium.sx"

    # Verify present in manifest
    man_data = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert man_data["site_url"] == "https://www.empornium.sx"

    # Verify ABSENT from _bbcode.txt
    bbcode_text = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert "https://www.empornium.sx" not in bbcode_text

    # 2. Build without site_url (defaults to empty string)
    out_dir_empty = tmp_path / "stage6_empty_site_url_out"
    out_dir_empty.mkdir()
    pack_title_empty = "Empty Site URL Pack"
    pack_dir_empty = out_dir_empty / pack_title_empty
    pack_dir_empty.mkdir()

    media_file_2 = pack_dir_empty / "scene_empty.mp4"
    media_file_2.write_bytes(b"\x00" * 1024)

    payload_empty = {
        "pack_title": pack_title_empty,
        "output_dir": str(out_dir_empty),
        "scenes": [{"id": 2, "path": str(media_file_2), "title": "Scene 2"}],
    }

    result_empty = task.run_build_megapack(payload_empty)
    assert result_empty["status"] == "success"
    assert result_empty["site_url"] == ""

    sub_data_empty = json.loads(Path(result_empty["submission_path"]).read_text(encoding="utf-8"))
    assert sub_data_empty["site_url"] == ""

