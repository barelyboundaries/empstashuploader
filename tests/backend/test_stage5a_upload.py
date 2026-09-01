"""
Tests for Stage 5a: Image Upload Seam with HamsterImg (degrade-and-continue).
Verifies opt-in gating, HamsterImg upload integration, 1:1 index alignment on partial failure,
single missing-key warning, and multipart real basename fix.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
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
from empornium_megapack.images import ContactSheetError, upload_hamster


def test_stage5a_upload_disabled_returns_file_urls(tmp_path):
    """When upload_previews is False (default), returns local file:/// URLs with zero network calls."""
    out_dir = tmp_path / "stage5a_disabled_out"
    out_dir.mkdir()
    pack_title = "Disabled Upload Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene1.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": False,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    with patch("empornium_megapack.images.upload_hamster") as mock_upload:
        result = task.run_build_megapack(payload)
        mock_upload.assert_not_called()

    assert result["status"] == "success"
    assert len(result["uploaded_urls"]) == 2
    assert all(u.startswith("file:///") for u in result["uploaded_urls"])
    assert result["preview_only"] is True


def test_stage5a_upload_enabled_successful_hamster_upload(tmp_path):
    """When upload_previews is True and key is configured, uploads contact sheets and gets remote URLs."""
    out_dir = tmp_path / "stage5a_success_out"
    out_dir.mkdir()
    pack_title = "Remote Upload Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene1.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "upload_previews": True,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    fake_settings = Settings(hamster_api_key="test-api-key")
    remote_url = "https://hamsterimg.net/images/2026/08/23/preview_1.jpg"

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings), \
         patch("empornium_megapack.images.upload_hamster", return_value=remote_url) as mock_upload:
        result = task.run_build_megapack(payload)
        assert mock_upload.call_count == 2

    assert result["status"] == "success"
    assert result["uploaded_urls"] == [remote_url, remote_url]
    assert result["preview_only"] is False

    bbcode = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert "PREVIEW ONLY" not in bbcode
    assert f"[url={remote_url}][img=200]{remote_url}[/img][/url]" in bbcode


def test_stage5a_missing_api_key_warns_once_and_falls_back(tmp_path, capsys):
    """When upload is enabled but API key is missing, warns once up-front and returns file:/// URLs without crashing."""
    out_dir = tmp_path / "stage5a_nokey_out"
    out_dir.mkdir()
    pack_title = "No Key Pack"
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

    fake_settings = Settings(hamster_api_key="")

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert len(result["uploaded_urls"]) == 4
    assert all(u.startswith("file:///") for u in result["uploaded_urls"])
    assert result["preview_only"] is True

    captured = capsys.readouterr()
    # Check that the missing key warning appears exactly once
    assert captured.err.count("HamsterImg upload enabled but no API key configured") == 1


def test_stage5a_degrade_and_continue_on_partial_failure(tmp_path, capsys):
    """When 1 sheet uploads and 1 sheet fails, preserves 1:1 index alignment, emits warning, and completes build."""
    out_dir = tmp_path / "stage5a_partial_out"
    out_dir.mkdir()
    pack_title = "Partial Failure Pack"
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
        if "preview_1" in str(image_path) and "thumb_" not in str(image_path):
            return remote_url1
        raise ContactSheetError("HTTP 500 Server Error")

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings), \
         patch("empornium_megapack.images.upload_hamster", side_effect=side_effect_upload):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert len(result["uploaded_urls"]) == 4
    assert result["uploaded_urls"][0] == remote_url1
    assert result["uploaded_urls"][1].startswith("file:///")
    # Partial failure means at least one local URL -> preview_only is True
    assert result["preview_only"] is True

    captured = capsys.readouterr()
    assert "Failed to upload contact sheet" in captured.err


def test_stage5a_multipart_filename_uses_real_basename(tmp_path):
    """images.upload_hamster passes the real image basename in multipart form data."""
    fake_img = tmp_path / "My_Custom_Contact_Sheet.jpg"
    fake_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    fake_settings = Settings(hamster_api_key="valid-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status_code": 200,
        "image": {"url": "https://hamsterimg.net/images/2026/08/23/My_Custom_Contact_Sheet.jpg"}
    }

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        url = upload_hamster(str(fake_img), settings=fake_settings)

    assert url == "https://hamsterimg.net/images/2026/08/23/My_Custom_Contact_Sheet.jpg"
    call_kwargs = mock_post.call_args.kwargs
    assert "files" in call_kwargs
    filename, fh, mime = call_kwargs["files"]["source"]
    assert filename == "My_Custom_Contact_Sheet.jpg"
    assert mime == "image/jpeg"
