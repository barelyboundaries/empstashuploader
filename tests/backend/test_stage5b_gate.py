"""
Tests for Stage 5b: Preview-Only Gate Behavior.
Verifies that all-remote URLs clear the preview gate, while local or mixed URLs
strictly maintain the [color=red][b]PREVIEW ONLY...[/b][/color] banner and preview_only=True flag.
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
from empornium_megapack.images import ContactSheetError


def test_stage5b_all_remote_urls_clears_preview_gate(tmp_path, capsys):
    """All remote URLs clear the banner, set preview_only=False, and emit no file:/// warnings."""
    out_dir = tmp_path / "stage5b_all_remote"
    out_dir.mkdir()
    pack_title = "All Remote Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file1 = pack_dir / "scene1.mp4"
    media_file2 = pack_dir / "scene2.mp4"
    media_file1.write_bytes(b"\x00" * 1024)
    media_file2.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": True,
        "scenes": [{"id": 1, "path": str(media_file1)}, {"id": 2, "path": str(media_file2)}],
    }

    fake_settings = Settings(hamster_api_key="test-api-key")
    remote_urls = [
        "https://hamsterimg.net/images/2026/08/23/sheet1.jpg",
        "https://hamsterimg.net/images/2026/08/23/sheet2.jpg",
        "https://hamsterimg.net/images/2026/08/23/thumb1.jpg",
        "https://hamsterimg.net/images/2026/08/23/thumb2.jpg",
    ]

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings), \
         patch("empornium_megapack.images.upload_hamster", side_effect=remote_urls):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert result["preview_only"] is False

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert "PREVIEW ONLY" not in bbcode
    assert "[url=https://hamsterimg.net/images/2026/08/23/sheet1.jpg]" in bbcode
    assert "[url=https://hamsterimg.net/images/2026/08/23/sheet2.jpg]" in bbcode

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["preview_only"] is False

    captured = capsys.readouterr()
    assert "BBCode contains local file:/// URLs" not in captured.err


def test_stage5b_all_local_urls_triggers_preview_gate(tmp_path, capsys):
    """When all URLs are file:///, the preview warning banner is present and preview_only=True."""
    out_dir = tmp_path / "stage5b_all_local"
    out_dir.mkdir()
    pack_title = "All Local Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_local.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": False,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert result["preview_only"] is True

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert bbcode.startswith("[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]")

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["preview_only"] is True

    captured = capsys.readouterr()
    assert "BBCode contains local file:/// URLs (preview only; do not post to public trackers)" in captured.err


def test_stage5b_mixed_urls_triggers_preview_gate(tmp_path, capsys):
    """When some URLs are remote and some are local (partial failure), the preview warning banner is present and preview_only=True."""
    out_dir = tmp_path / "stage5b_mixed"
    out_dir.mkdir()
    pack_title = "Mixed Local Remote Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file1 = pack_dir / "scene1.mp4"
    media_file2 = pack_dir / "scene2.mp4"
    media_file1.write_bytes(b"\x00" * 1024)
    media_file2.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": True,
        "scenes": [{"id": 1, "path": str(media_file1)}, {"id": 2, "path": str(media_file2)}],
    }

    fake_settings = Settings(hamster_api_key="test-api-key")
    remote_url1 = "https://hamsterimg.net/images/2026/08/23/sheet1.jpg"

    def side_effect_upload(image_path, settings=None):
        if "preview_1" in str(image_path):
            return remote_url1
        raise ContactSheetError("Network timeout during upload")

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings), \
         patch("empornium_megapack.images.upload_hamster", side_effect=side_effect_upload):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert result["preview_only"] is True

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert bbcode.startswith("[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]")
    assert f"[url={remote_url1}]" in bbcode
    assert "[url=file:///" in bbcode

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["preview_only"] is True

    captured = capsys.readouterr()
    assert "BBCode contains local file:/// URLs (preview only; do not post to public trackers)" in captured.err
