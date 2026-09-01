"""
Tests for Stage 5c: Announce URL & Passkey Masking.
Verifies that raw announce URLs and passkeys appear only inside the binary .torrent file,
and are masked via sanitize_announce_url on all logs, manifests, submission payloads, and return dicts.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
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
from empornium_megapack.config import Settings
from empornium_megapack.torrents import sanitize_announce_url, validate_announce_url


def test_stage5c_passkey_masked_in_manifest_and_submission(tmp_path, capsys):
    """Raw passkey appears in .torrent only; manifests, submissions, and logs use sanitized announce."""
    out_dir = tmp_path / "stage5c_masking_out"
    out_dir.mkdir()
    pack_title = "Masking Test Megapack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene_mask.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    raw_passkey = "a1b2c3d4e5f678901234567890abcdef"
    raw_announce = f"http://tracker.empornium.sx:2710/{raw_passkey}/announce"
    fake_settings = Settings(empornium_announce_url=raw_announce)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    masked = sanitize_announce_url(raw_announce)

    # 1. Result dictionary must carry sanitized announce
    assert result["announce_url"] == masked
    assert raw_passkey not in str(result["announce_url"])
    assert result["source"] == "Emp"

    # 2. Manifest file must carry sanitized announce
    manifest_data = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest_data["announce_url"] == masked
    assert raw_passkey not in str(manifest_data)

    # 3. Submission file must carry sanitized announce
    sub_data = json.loads(Path(result["submission_path"]).read_text(encoding="utf-8"))
    assert sub_data["announce_url"] == masked
    assert raw_passkey not in str(sub_data)

    # 4. BBCode text must NOT contain the passkey
    bbcode_text = Path(result["bbcode_path"]).read_text(encoding="utf-8")
    assert raw_passkey not in bbcode_text

    # 5. Stderr logs must NOT contain the passkey
    captured = capsys.readouterr()
    assert raw_passkey not in captured.err
    assert raw_passkey not in captured.out

    # 6. Binary .torrent file MUST contain the raw announce and be marked private
    torrent = torf.Torrent.read(result["torrent_path"])
    assert torrent.trackers == [[raw_announce]]
    assert torrent.private is True
    assert torrent.source == "Emp"


def test_stage5c_malformed_announce_url_raises_error(tmp_path):
    """A malformed announce URL raises an error and halts the build."""
    out_dir = tmp_path / "stage5c_malformed_out"
    out_dir.mkdir()
    pack_title = "Malformed Announce Pack"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    fake_settings = Settings(empornium_announce_url="ftp://invalid.host/path")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        with pytest.raises(RuntimeError, match="Invalid announce URL"):
            task.run_build_megapack(payload)


def test_stage5c_payload_announce_ignored_without_explicit_opt_in(tmp_path):
    """Amendment B7: Payload announce is ignored unless allow_custom_announce=True."""
    out_dir = tmp_path / "stage5c_optin_out"
    out_dir.mkdir()
    pack_title = "Payload Announce Ignored"
    pack_dir = out_dir / pack_title
    pack_dir.mkdir()

    media_file = pack_dir / "scene.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    configured_announce = "http://tracker.empornium.sx:2710/configuredpasskey/announce"
    fake_settings = Settings(empornium_announce_url=configured_announce)

    untrusted_payload_announce = "http://tracker.empornium.sx:2710/untrustedfrombrowser/announce"

    payload_default = {
        "pack_title": pack_title,
        "output_dir": str(out_dir),
        "announce": untrusted_payload_announce,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }

    # Case 1: Default (no opt-in) -> uses configured announce
    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        res1 = task.run_build_megapack(payload_default)

    tor1 = torf.Torrent.read(res1["torrent_path"])
    assert tor1.trackers == [[configured_announce]]

    # Case 2: Explicit opt-in -> accepts payload announce after validation
    payload_opt_in = {
        "pack_title": "Payload Announce Opted In",
        "output_dir": str(out_dir),
        "announce": untrusted_payload_announce,
        "allow_custom_announce": True,
        "scenes": [{"id": 1, "path": str(media_file)}],
    }
    optin_pack_dir = out_dir / "Payload Announce Opted In"
    optin_pack_dir.mkdir()
    media_file_optin = optin_pack_dir / "scene.mp4"
    media_file_optin.write_bytes(b"\x00" * 1024)
    payload_opt_in["scenes"] = [{"id": 1, "path": str(media_file_optin)}]

    with patch("empornium_megapack.config.get_settings", return_value=fake_settings):
        res2 = task.run_build_megapack(payload_opt_in)

    tor2 = torf.Torrent.read(res2["torrent_path"])
    assert tor2.trackers == [[untrusted_payload_announce]]
