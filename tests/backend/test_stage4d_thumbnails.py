"""
Tests for Stage 4d: Clickable Thumbnails and the file:/// Preview-Only Gate.
Verifies that generated BBCode uses clickable [url={url}][img=200]{url}[/img][/url]
markup and that local file:/// URLs trigger the preview-only gate and warning.
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
from empornium_megapack.metadata import THUMB_WIDTH


def test_stage4d_thumbnail_markup_and_file_url_gate(tmp_path, capsys):
    """Local file:/// URLs produce thumbnail markup, set preview_only=True, and log a warning."""
    out_dir = tmp_path / "stage4d_local_out"
    out_dir.mkdir()
    pack_title = "Local Preview Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_local.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert result["preview_only"] is True

    # Check BBCode content on disk
    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert bbcode.startswith("[color=red][b]PREVIEW ONLY: Contains local file:/// URLs[/b][/color]")
    assert f"[img={THUMB_WIDTH}]" in bbcode
    safe_full = task._sanitize_image_url(result["uploaded_urls"][0])
    safe_thumb = task._sanitize_image_url(result["uploaded_urls"][1])
    assert f"[url={safe_full}][img={THUMB_WIDTH}]{safe_thumb}[/img][/url]" in bbcode

    # Check manifest on disk
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["preview_only"] is True

    # Check stderr for the native warning
    captured = capsys.readouterr()
    assert "\x01w\x02BBCode contains local file:/// URLs (preview only; do not post to public trackers)" in captured.err


def test_stage4d_remote_urls_preview_only_false(tmp_path, capsys):
    """Remote HTTPS URLs produce thumbnail markup, set preview_only=False, and emit no file:/// warning."""
    out_dir = tmp_path / "stage4d_remote_out"
    out_dir.mkdir()
    pack_title = "Remote Hosted Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_remote.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 2, "path": str(media_file)}],
    }

    remote_urls = ["https://imgbox.com/sample_preview_1.jpg"]

    with patch.object(task, "upload_previews", return_value=remote_urls):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert result["preview_only"] is False

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert "PREVIEW ONLY" not in bbcode
    assert "[url=https://imgbox.com/sample_preview_1.jpg][img=200]https://imgbox.com/sample_preview_1.jpg[/img][/url]" in bbcode

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["preview_only"] is False

    captured = capsys.readouterr()
    assert "BBCode contains local file:/// URLs" not in captured.err


def test_stage4d_urls_with_brackets_and_spaces_not_html_escaped(tmp_path):
    """Image URLs containing brackets and spaces are URL-quoted without converting brackets to HTML entities &#91;."""
    out_dir = tmp_path / "stage4d_url_bracket_out"
    out_dir.mkdir()
    pack_title = "Bracket Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_bracket.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 3, "path": str(media_file)}],
    }

    raw_remote_url = "https://imagehost.example.com/galleries/Pack [1080p] Final/preview [1].jpg"
    with patch.object(task, "upload_previews", return_value=[raw_remote_url]):
        result = task.run_build_megapack(payload)

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    # Brackets must NOT be turned into &#91; inside the image link
    assert "&#91;" not in bbcode.split("[hr]")[-1]
    assert "&#93;" not in bbcode.split("[hr]")[-1]
    expected_url = "https://imagehost.example.com/galleries/Pack%20%5B1080p%5D%20Final/preview%20%5B1%5D.jpg"
    assert f"[url={expected_url}]" in bbcode
    assert f"[img={THUMB_WIDTH}]{expected_url}[/img]" in bbcode

    # Assert no emitted [url=...] contains a bare ] or [ before its closing bracket
    import re
    url_tag_matches = re.findall(r"\[url=([^\]]+)\]", bbcode)
    assert len(url_tag_matches) > 0
    for target in url_tag_matches:
        assert "[" not in target, f"URL target '{target}' contains unencoded ["
        assert "]" not in target, f"URL target '{target}' contains unencoded ]"
        assert " " not in target, f"URL target '{target}' contains unencoded space"
