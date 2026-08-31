"""
Tier 5 Integration & IPC Adversarial Verification Suite.

Comprehensive white-box adversarial stress testing covering:
1. Stash IPC stream protocol formatting (\x01p\x02, \x01i\x02, \x01w\x02, \x01e\x02), boundary clamping, UTF-8 safety.
2. Stdin payload parsing across Stash PluginArgInput shapes, malformed inputs, CLI overrides, and server connections.
3. Atomic lockfile concurrency, stale PID detection, expired timestamps, corrupt locks, and cleanup guarantees.
4. Win32 handle creation times, process lifecycle inspection (is_pid_running), ctypes safety, and fallback paths.
5. Volume serial numbers, drive letter comparisons, UNC paths, and hardlink feasibility.
6. Target directory cleanliness pre-validation, case folding, foreign directory/file rejection, and allowed artifact filters.
7. End-to-end integration task workflows under edge conditions (ProbeFiles and BuildMegapack).
"""

import os
import sys
import io
import json
import time
import math
import shutil
import ctypes
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from unittest.mock import patch, MagicMock

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PLUGIN_DIR = ROOT_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task as task_module
from task import (
    emit_progress,
    check_dependencies,
    normalize_grid_layout,
    _generate_pillow_placeholder,
    generate_contact_sheet,
    upload_previews,
    get_win32_creation_time,
    get_volume_serial_number,
    can_hardlink,
    validate_pack_files_present,
    is_pid_running,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
    _extract_names,
    _extract_scene_paths,
)
import torf


def _create_dummy_video(path: Path, size_bytes: int = 1024 * 64) -> Path:
    """Helper to create dummy media file with predictable content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SAMPLE_VIDEO_DATA_" + b"\x00" * (size_bytes - 18))
    return path


# ============================================================================
# 1. STASH IPC PROTOCOL FORMATTING & ESCAPE HANDLING
# ============================================================================

class TestStashIPCProtocolFormatting:
    """Adversarial stress-testing of Stash native IPC stderr streams."""

    def test_emit_progress_boundary_clamping(self, monkeypatch):
        """emit_progress clamps out-of-range floats to [0.0, 1.0] with 4-decimal precision."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        test_cases = [
            (-10.0, "\x01p\x020.0000\n"),
            (-0.0001, "\x01p\x020.0000\n"),
            (0.0, "\x01p\x020.0000\n"),
            (0.5, "\x01p\x020.5000\n"),
            (0.123456, "\x01p\x020.1235\n"),
            (1.0, "\x01p\x021.0000\n"),
            (1.0001, "\x01p\x021.0000\n"),
            (999.9, "\x01p\x021.0000\n"),
        ]

        for val, expected in test_cases:
            captured_stderr.truncate(0)
            captured_stderr.seek(0)
            emit_progress(val)
            assert captured_stderr.getvalue() == expected

    def test_emit_progress_non_finite_and_invalid_types(self, monkeypatch):
        """emit_progress safely handles NaN, Infs, None, and arbitrary non-numeric types without throwing."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        invalid_inputs = [
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            "not_a_number",
            {"progress": 0.5},
            [0.5],
            object(),
        ]

        for invalid in invalid_inputs:
            captured_stderr.truncate(0)
            captured_stderr.seek(0)
            emit_progress(invalid)
            assert captured_stderr.getvalue() == "\x01p\x020.0000\n"

    def test_emit_progress_with_info_message(self, monkeypatch):
        """emit_progress with message emits both \x01p\x02 and \x01i\x02 with percentage prefix."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        emit_progress(0.42, "Hashing torrent files")
        output = captured_stderr.getvalue()

        assert "\x01p\x020.4200\n" in output
        assert "\x01i\x02[42%] Hashing torrent files\n" in output

    def test_emit_progress_unicode_emoji_and_special_chars(self, monkeypatch):
        """emit_progress handles high-plane emojis, CJK, Cyrillic, and quotes in status messages."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        complex_msg = "🚀 Processing scene 1: 東京 / 東京都 & 'Special' \"Quotes\" — 100% complete! \u200b\u00a9"
        emit_progress(0.95, complex_msg)
        output = captured_stderr.getvalue()

        assert "\x01p\x020.9500\n" in output
        assert f"\x01i\x02[95%] {complex_msg}\n" in output

    def test_ipc_prefix_cleanliness_no_cross_contamination(self, monkeypatch):
        """Verify exact control character bytes: \x01 and \x02 delimiters are uncorrupted."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        emit_progress(0.75, "Step 3")
        lines = captured_stderr.getvalue().splitlines()

        assert len(lines) == 2
        assert lines[0].startswith("\x01p\x02")
        assert lines[0][3:] == "0.7500"
        assert lines[1].startswith("\x01i\x02")
        assert lines[1][3:] == "[75%] Step 3"

    def test_check_dependencies_missing_binary_warning_ipc(self, monkeypatch):
        """check_dependencies emits \x01w\x02 warning when vcsi or ffmpeg is not found on PATH."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        with patch("shutil.which", return_value=None):
            check_dependencies()

        out = captured_stderr.getvalue()
        assert "\x01w\x02Missing binaries:" in out
        assert "vcsi" in out
        assert "ffmpeg" in out


# ============================================================================
# 2. STDIN PAYLOAD PARSING ADVERSARIAL
# ============================================================================

class TestStdinPayloadParsingAdversarial:
    """Adversarial tests for parse_input_payload() across varied Stash plugin input shapes."""

    def test_parse_input_payload_stash_plugin_value_input_list(self, monkeypatch):
        """Parses Stash standard PluginValueInput list payload format."""
        payload_data = {
            "pack_title": "StashTestPack",
            "scenes": [{"id": "1", "path": "C:/media/scene1.mp4"}],
            "output_dir": "C:/Megapacks",
        }
        stash_input = {
            "task_name": "BuildMegapack",
            "server_connection": {"Scheme": "http", "Host": "localhost", "Port": 9999, "ApiKey": "test_key"},
            "args": [
                {"key": "mode", "value": {"str": "build"}},
                {"key": "payload", "value": json.dumps(payload_data)},
            ],
        }

        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(stash_input))

        mode, payload, server_conn = parse_input_payload()
        assert mode == "build"
        assert payload["pack_title"] == "StashTestPack"
        assert payload["scenes"][0]["path"] == "C:/media/scene1.mp4"
        assert server_conn.get("ApiKey") == "test_key"

    def test_parse_input_payload_dict_args_format(self, monkeypatch):
        """Parses dictionary-structured args payload."""
        stash_input = {
            "task_name": "ProbeFiles",
            "server_connection": {},
            "args": {
                "mode": "probe",
                "payload": {"files": ["C:/media/1.mp4", "C:/media/2.mp4"], "target_dir": "C:/out"},
            },
        }

        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(stash_input))

        mode, payload, _ = parse_input_payload()
        assert mode == "probe"
        assert len(payload["files"]) == 2
        assert payload["target_dir"] == "C:/out"

    def test_parse_input_payload_flat_dict_args(self, monkeypatch):
        """Parses flat dictionary args where payload fields are directly in args."""
        stash_input = {
            "args": {
                "pack_title": "FlatPack",
                "notes": "Direct note",
                "output_dir": "C:/Packs",
            }
        }

        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(stash_input))

        mode, payload, _ = parse_input_payload()
        assert mode == "build"
        assert payload["pack_title"] == "FlatPack"
        assert payload["notes"] == "Direct note"

    def test_parse_input_payload_raw_json_string_args(self, monkeypatch):
        """Parses stringified JSON in args."""
        stash_input = {
            "args": json.dumps({"pack_title": "StringifiedJsonPack", "layout": "3x3"})
        }

        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(stash_input))

        mode, payload, _ = parse_input_payload()
        assert payload["pack_title"] == "StringifiedJsonPack"
        assert payload["layout"] == "3x3"

    def test_parse_input_payload_malformed_json_and_empty_stdin(self, monkeypatch):
        """Handles malformed JSON, empty stdin, whitespace gracefully."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        for bad_input in ["{ malformed json", "", "   \n\t  ", "null", "12345", "[1, 2, 3]"]:
            captured_stderr.truncate(0)
            captured_stderr.seek(0)
            monkeypatch.setattr(sys.stdin, "read", lambda bi=bad_input: bi)
            mode, payload, server_conn = parse_input_payload()
            assert isinstance(payload, dict)
            assert isinstance(server_conn, dict)
            assert mode in ("build", "probe")

    def test_parse_input_payload_cli_argv_precedence(self, monkeypatch):
        """CLI argv[1] containing 'probe' sets mode to probe even with empty stdin."""
        monkeypatch.setattr(sys, "argv", ["task.py", "probe"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        mode, payload, server_conn = parse_input_payload()
        assert mode == "probe"


# ============================================================================
# 3. ATOMIC LOCKFILE CONCURRENCY & STALE MANAGEMENT
# ============================================================================

class TestLockfileConcurrencyAndStaleManagement:
    """Adversarial stress-testing of atomic lockfiles, stale detection, and cleanup."""

    # These tests mock task.create_torrent, so post-generation torrent
    # verification cannot run — its correctness is covered by test_torrents.py
    # + test_build_seed_scratch.py. Hence task._verify_torrent_exact_set is
    # patched alongside create_torrent in every test below.

    def test_lockfile_atomic_acquisition_and_cleanup(self, tmp_path):
        """Lockfile is created atomically with PID metadata and removed upon clean build completion."""
        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()
        pack_title = "LockTest"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene1.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "include_contact_sheets": False,
            "scenes": [{"path": str(media_file)}],
        }

        with patch("task.create_torrent"), \
             patch("task._verify_torrent_exact_set"), \
             patch("task.write_manifest"), \
             patch("task.generate_contact_sheet", return_value=str(target_dir / "LockTest_preview.jpg")):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        # Lockfile should be deleted
        lock_path = target_dir / ".LockTest.lock"
        assert not lock_path.exists()

    def test_lockfile_active_process_collision_rejection(self, tmp_path):
        """Concurrent build for the same pack title raises RuntimeError if another active PID holds the lock."""
        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()
        pack_title = "ActiveLockPack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene1.mp4")

        lock_path = target_dir / ".ActiveLockPack.lock"
        lock_path.write_text(
            f"pid={os.getpid()}\nstarted={time.time()}\npack=ActiveLockPack\n",
            encoding="utf-8",
        )

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [{"path": str(media_file)}],
        }

        with pytest.raises(RuntimeError, match="Concurrent build in progress"):
            run_build_megapack(payload)

        # Ensure the active lockfile was NOT deleted by the rejected build
        assert lock_path.exists()
        lock_path.unlink()

    def test_lockfile_stale_dead_pid_reclamation(self, tmp_path, monkeypatch):
        """Stale lockfile held by a non-existent/dead PID is reclaimed and build succeeds."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()
        pack_title = "DeadPidPack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene1.mp4")

        lock_path = target_dir / ".DeadPidPack.lock"
        lock_path.write_text(
            f"pid=99999999\nstarted={time.time()}\npack=DeadPidPack\n",
            encoding="utf-8",
        )

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "include_contact_sheets": False,
            "scenes": [{"path": str(media_file)}],
        }

        with patch("task.is_pid_running", return_value=False), \
             patch("task.create_torrent"), \
             patch("task._verify_torrent_exact_set"), \
             patch("task.write_manifest"), \
             patch("task.generate_contact_sheet", return_value=str(target_dir / "DeadPidPack_preview.jpg")):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        out = captured_stderr.getvalue()
        assert "\x01w\x02Reclaiming stale lockfile from dead/timed-out process:" in out
        assert not lock_path.exists()

    def test_lockfile_stale_expired_timestamp_reclamation(self, tmp_path, monkeypatch):
        """Lockfile older than 3600 seconds is reclaimed even if PID check is inconclusive."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()
        pack_title = "ExpiredLockPack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene1.mp4")

        lock_path = target_dir / ".ExpiredLockPack.lock"
        old_time = time.time() - 7200  # 2 hours ago
        lock_path.write_text(
            f"pid={os.getpid()}\nstarted={old_time}\npack=ExpiredLockPack\n",
            encoding="utf-8",
        )

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "include_contact_sheets": False,
            "scenes": [{"path": str(media_file)}],
        }

        with patch("task.create_torrent"), \
             patch("task._verify_torrent_exact_set"), \
             patch("task.write_manifest"), \
             patch("task.generate_contact_sheet", return_value=str(target_dir / "ExpiredLockPack_preview.jpg")):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        out = captured_stderr.getvalue()
        assert "\x01w\x02Reclaiming stale lockfile" in out

    def test_lockfile_corrupted_empty_or_garbage_reclamation(self, tmp_path, monkeypatch):
        """Corrupted, zero-byte, or partial lockfile content is detected as stale and reclaimed."""
        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()
        pack_title = "CorruptPack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene1.mp4")

        for corrupt_content in ["", "   ", "pid=abc\nstarted=bad\n", "garbage non-standard format"]:
            lock_path = target_dir / ".CorruptPack.lock"
            lock_path.write_text(corrupt_content, encoding="utf-8")

            payload = {
                "pack_title": pack_title,
                "output_dir": str(target_dir),
                "include_contact_sheets": False,
                "scenes": [{"path": str(media_file)}],
            }

            with patch("task.create_torrent"), \
                 patch("task._verify_torrent_exact_set"), \
                 patch("task.write_manifest"), \
                 patch("task.generate_contact_sheet", return_value=str(target_dir / "CorruptPack_preview.jpg")):
                res = run_build_megapack(payload)
                assert res["status"] == "success"
            assert not lock_path.exists()

    def test_lockfile_cleanup_on_build_failure(self, tmp_path):
        """Lockfile is cleaned up in finally block when build fails due to empty media payload."""
        target_dir = tmp_path / "LockPack"
        target_dir.mkdir()

        payload = {
            "pack_title": "FailingPack",
            "output_dir": str(target_dir),
            "scenes": [],
        }

        with pytest.raises(RuntimeError, match="No valid media files"):
            run_build_megapack(payload)

        lock_path = target_dir / ".FailingPack.lock"
        assert not lock_path.exists()


# ============================================================================
# 4. WIN32 HANDLE & PROCESS LIFECYCLE
# ============================================================================

class TestWin32HandleAndProcessLifecycle:
    """Adversarial tests for Win32 API interactions, PID lifecycle, and creation timestamps."""

    def test_is_pid_running_current_process(self):
        """is_pid_running(os.getpid()) returns True for the current running process."""
        assert is_pid_running(os.getpid()) is True

    def test_is_pid_running_non_existent_pid(self):
        """is_pid_running returns False for dead/non-existent process PID."""
        assert is_pid_running(99999999) is False

    def test_is_pid_running_boundary_and_invalid_inputs(self):
        """is_pid_running handles negative PIDs, zero, None, strings safely."""
        invalid_pids = [-1, 0, -999, None, "12345", 3.14, [], {}]
        for bad_pid in invalid_pids:
            assert is_pid_running(bad_pid) is False

    def test_get_win32_creation_time_real_file(self, tmp_path):
        """get_win32_creation_time returns a valid positive float for existing files."""
        temp_file = tmp_path / "test_ctime.mp4"
        _create_dummy_video(temp_file)

        ctime = get_win32_creation_time(str(temp_file))
        assert isinstance(ctime, float)
        assert ctime > 0.0
        assert abs(time.time() - ctime) < 300.0

    def test_get_win32_creation_time_nonexistent_and_invalid_paths(self):
        """get_win32_creation_time returns 0.0 safely for invalid or missing files."""
        invalid_paths = [
            "C:/nonexistent/path/does_not_exist.mp4",
            "",
            None,
            12345,
            [],
            "NUL",
        ]
        for p in invalid_paths:
            assert get_win32_creation_time(p) == 0.0

    def test_get_win32_creation_time_exception_fallback(self, tmp_path):
        """get_win32_creation_time falls back to os.path.getctime if review import fails."""
        temp_file = tmp_path / "test_fallback.mp4"
        _create_dummy_video(temp_file)

        with patch("os.path.getctime", return_value=1234567.89):
            ctime = get_win32_creation_time(str(temp_file))
            assert ctime > 0.0


# ============================================================================
# 5. VOLUME SERIAL NUMBERS & HARDLINK FEASIBILITY
# ============================================================================

class TestVolumeSerialNumberAndHardlinkFeasibility:
    """Adversarial tests for volume serial numbers and cross-volume hardlink checks."""

    def test_get_volume_serial_number_current_drive(self, tmp_path):
        """get_volume_serial_number returns non-None integer on Windows for valid drive."""
        vol = get_volume_serial_number(str(tmp_path))
        if os.name == "nt":
            assert vol is not None
            assert isinstance(vol, int)

    def test_get_volume_serial_number_invalid_paths(self):
        """get_volume_serial_number returns None on invalid inputs or non-existent drives."""
        for bad_path in ["", None, 12345, "Z:\\impossible_drive\\test"]:
            res = get_volume_serial_number(bad_path)
            assert res is None or isinstance(res, int)

    def test_can_hardlink_same_volume_and_drive(self, tmp_path):
        """can_hardlink returns True for files on the same volume/drive."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        file1 = _create_dummy_video(dir1 / "video.mp4")

        assert can_hardlink(str(file1), str(dir2)) is True

    def test_can_hardlink_different_volumes_mocked(self):
        """can_hardlink returns False when volume serial numbers differ."""
        with patch("task.get_volume_serial_number", side_effect=[111111, 222222]):
            assert can_hardlink("C:/file1.mp4", "D:/target") is False

    def test_can_hardlink_drive_letter_fallback(self):
        """can_hardlink falls back to drive letter comparison when volume serial numbers are None."""
        with patch("task.get_volume_serial_number", return_value=None):
            assert can_hardlink("C:/Media/scene.mp4", "C:/Target/Pack") is True
            assert can_hardlink("C:/Media/scene.mp4", "D:/Target/Pack") is False

    def test_can_hardlink_empty_and_none_paths(self):
        """can_hardlink safely returns False on empty or None arguments."""
        assert can_hardlink("", "C:/Target") is False
        assert can_hardlink("C:/Source.mp4", "") is False
        assert can_hardlink(None, "C:/Target") is False
        assert can_hardlink("C:/Source.mp4", None) is False


# ============================================================================
# 6. PACK-FILE-PRESENCE VALIDATION (replaces foreign-file cleanliness, T3)
# ============================================================================

class TestPackFilePresenceAdversarial:
    """Adversarial stress-testing of pack-file-presence pre-validation.

    OLD→NEW (T3): the former TestForeignFileFilteringAdversarial pinned the
    deleted foreign-file scan (allowlists, refusal counts). Presence validation
    never scans the directory: unrelated entries are ignored, and only missing
    expected primaries raise."""

    def test_validate_pack_files_present_case_insensitive_matching(self, tmp_path):
        """Declared vs on-disk casing may differ on Windows (SCENE01.MP4 matches scene01.mp4)."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = _create_dummy_video(target_dir / "SCENE01.MP4")

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(target_dir / "scene01.mp4")],
        )

    def test_validate_pack_files_present_ignores_standard_artifacts(self, tmp_path):
        """OLD→NEW: the old allowlist admitted .torrent/_manifest/_bbcode/_preview/
        .lock/Contact Sheets entries; the allowlist concept is deleted — the
        validator never looks at non-expected entries at all."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = _create_dummy_video(target_dir / "scene01.mp4")

        (target_dir / "MyPack.torrent").write_bytes(b"torrent_data")
        (target_dir / "MyPack_manifest.json").write_text("{}", encoding="utf-8")
        (target_dir / "MyPack_bbcode.txt").write_text("[b]BBCode[/b]", encoding="utf-8")
        (target_dir / "MyPack_preview.jpg").write_bytes(b"preview_image")
        (target_dir / "MyPack_preview_1.jpg").write_bytes(b"preview_image_1")
        (target_dir / ".MyPack.lock").write_text("lock_data", encoding="utf-8")
        (target_dir / "Contact Sheets").mkdir()
        (target_dir / "contact_sheets").mkdir()

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )

    def test_validate_pack_files_present_ignores_stray_directories_and_files(self, tmp_path):
        """OLD→NEW: stray subdirectories and data files formerly triggered
        "contains 2 foreign file" refusal; they are now ignored entirely."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = _create_dummy_video(target_dir / "scene01.mp4")

        (target_dir / "unrelated_subfolder").mkdir()
        (target_dir / "stray_document.pdf").write_bytes(b"pdf_data")

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )

    def test_validate_pack_files_present_lists_every_missing_path(self, tmp_path):
        """Error message names each missing expected path verbatim (the old
        formatter listed the first 5 foreign files; the new one must not
        truncate the missing list — every path is accounted for)."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()

        missing_paths = [str(target_dir / f"scene_{i:02d}.mp4") for i in range(1, 11)]

        with pytest.raises(RuntimeError, match="missing from") as exc_info:
            validate_pack_files_present(
                str(target_dir),
                expected_primary_paths=missing_paths,
            )
        msg = str(exc_info.value)
        for missing in missing_paths:
            assert missing in msg
        assert "Run Consolidate or add the missing files to the seed directory" in msg

    def test_validate_pack_files_present_empty_dir_and_missing_dir(self, tmp_path):
        """OLD→NEW: an empty dir with no expected files passes (as before); a
        NON-EXISTENT dir formerly early-returned OK, but with an expected file
        it is now a missing-file refusal — nothing can exist under a dir that
        does not exist."""
        empty_dir = tmp_path / "EmptyDir"
        empty_dir.mkdir()
        validate_pack_files_present(str(empty_dir), [])

        nonexistent_dir = tmp_path / "DoesNotExist"
        with pytest.raises(RuntimeError, match="missing from"):
            validate_pack_files_present(str(nonexistent_dir), [str(nonexistent_dir / "scene.mp4")])


# ============================================================================
# 7. PROBEFILES INTEGRATION ADVERSARIAL
# ============================================================================

class TestProbeFilesIntegrationAdversarial:
    """Adversarial tests for run_probe_files() across varied scene file structures."""

    def test_run_probe_files_diverse_scene_structures(self, tmp_path):
        """Probes scene files presented as strings, Paths, dicts with 'path', 'source_path', 'file_paths', 'files'."""
        dir1 = tmp_path / "scenes"
        dir1.mkdir()
        f1 = _create_dummy_video(dir1 / "scene1.mp4")
        f2 = _create_dummy_video(dir1 / "scene2.mkv")
        f3 = _create_dummy_video(dir1 / "scene3.avi")

        payload = {
            "target_dir": str(tmp_path),
            "files": [
                str(f1),
                f2,
                {"id": "101", "path": str(f3)},
                {"id": "102", "source_path": str(f1)},
                {"id": "103", "file_paths": [str(f2)]},
                {"id": "104", "files": [{"path": str(f3)}]},
                {"id": "105", "path": "C:/nonexistent/missing.mp4"},
            ],
        }

        res = run_probe_files(payload)
        assert res["status"] == "success"
        assert res["task"] == "ProbeFiles"
        assert len(res["files"]) == 7

        assert res["files"][0]["exists"] is True
        assert res["files"][0]["size"] == 64 * 1024
        assert res["files"][0]["creation_time"] > 0.0

        assert res["files"][6]["exists"] is False
        assert res["files"][6]["size"] == 0
        assert res["files"][6]["creation_time"] == 0.0

    def test_run_probe_files_case_insensitive_duplicate_counting(self, tmp_path):
        """Duplicate detection is case-insensitive across filenames."""
        d1 = tmp_path / "folder1"
        d2 = tmp_path / "folder2"
        d1.mkdir()
        d2.mkdir()
        f1 = _create_dummy_video(d1 / "SceneVideo.mp4")
        f2 = _create_dummy_video(d2 / "scenevideo.mp4")

        payload = {
            "target_dir": str(tmp_path),
            "files": [str(f1), str(f2)],
        }

        res = run_probe_files(payload)
        assert res["duplicate_count"] == 1
        assert res["files"][0]["is_duplicate_name"] is True
        assert res["files"][1]["is_duplicate_name"] is True


# ============================================================================
# 8. BUILDMEGAPACK INTEGRATION EDGE CASES
# ============================================================================

class TestBuildMegapackIntegrationEdgeCases:
    """Adversarial tests for run_build_megapack() workflows under edge conditions."""

    def test_build_megapack_zero_valid_files_rejection(self, tmp_path, monkeypatch):
        """Building with zero valid/existing media files raises RuntimeError and emits \x01e\x02."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        payload = {
            "pack_title": "EmptyTestPack",
            "output_dir": str(tmp_path),
            "scenes": [{"path": "C:/missing/video1.mp4"}, {"path": "C:/missing/video2.mp4"}],
        }

        with pytest.raises(RuntimeError, match="No valid media files found"):
            run_build_megapack(payload)

        out = captured_stderr.getvalue()
        assert "\x01e\x02No valid media files found in scenes payload." in out

    def test_build_megapack_basename_collision_rejection(self, tmp_path, monkeypatch):
        """Building with colliding scene basenames raises RuntimeError and blocks torrent generation."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        d1 = tmp_path / "source1"
        d2 = tmp_path / "source2"
        f1 = _create_dummy_video(d1 / "collision.mp4")
        f2 = _create_dummy_video(d2 / "COLLISION.mp4")

        payload = {
            "pack_title": "CollisionPack",
            "output_dir": str(tmp_path / "out"),
            "scenes": [{"path": str(f1)}, {"path": str(f2)}],
        }

        with pytest.raises(RuntimeError, match="Basename collision detected"):
            run_build_megapack(payload)

        out = captured_stderr.getvalue()
        assert "\x01e\x02Basename collision detected" in out

    def test_build_megapack_pillow_fallback_on_contact_sheet_failure(self, tmp_path, monkeypatch):
        """When VCSI contact sheet generation fails or times out, Pillow placeholder is generated."""
        captured_stderr = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        target_dir = tmp_path / "PillowPack"
        target_dir.mkdir()
        pack_title = "PillowFallbackPack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene01.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [{"path": str(media_file), "title": "Fallback Scene"}],
            "trackers": ["http://tracker.example.com/announce"],
        }

        with patch("task._domain_generate_contact_sheet", return_value=False):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        out = captured_stderr.getvalue()
        assert "\x01w\x02vcsi generation failed" in out
        assert "Falling back to Pillow placeholder" in out

        assert len(res["contact_sheets"]) == 1
        assert os.path.exists(res["contact_sheets"][0])
        assert os.path.getsize(res["contact_sheets"][0]) > 0

    def test_build_megapack_unicode_and_special_character_metadata(self, tmp_path):
        """Megapack builds cleanly with Japanese, emojis, quotes, and HTML-like tags in titles and notes."""
        target_dir = tmp_path / "UnicodePack"
        target_dir.mkdir()

        complex_title = "メガパック 2026: 🚀 'Greatest' & <Special> [Collection]"
        complex_notes = "Notes with multi-line\nquotes & 'single' \"double\" <escapes> 💖"
        pack_dir = target_dir / task_module.sanitize_name(complex_title)
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene01.mp4")

        payload = {
            "pack_title": complex_title,
            "output_dir": str(target_dir),
            "include_contact_sheets": False,
            "scenes": [{"path": str(media_file), "performers": ["🌸 Aoi", "Ken"], "tags": ["HD", "4K"]}],
            "notes": complex_notes,
            "trackers": ["http://tracker.example.com/announce"],
        }

        with patch("task.generate_contact_sheet", return_value=str(target_dir / "preview.jpg")):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        manifest_path = Path(res["manifest_path"])
        assert manifest_path.exists()
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_data["pack_title"] == complex_title

        bbcode_path = Path(res["bbcode_path"])
        assert bbcode_path.exists()
        bbcode_text = bbcode_path.read_text(encoding="utf-8")
        assert "メガパック 2026" in bbcode_text
        assert "🌸 Aoi" in bbcode_text
        assert complex_notes in bbcode_text

    def test_build_megapack_include_contact_sheets_subfolder(self, tmp_path):
        """When include_contact_sheets is True, contact sheets are copied to 'Contact Sheets' subdirectory inside pack_dir."""
        target_dir = tmp_path / "CS_Pack"
        target_dir.mkdir()
        pack_title = "CS_Subdir_Pack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene01.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [{"path": str(media_file)}],
            "include_contact_sheets": True,
        }

        preview_file = target_dir / "CS_Subdir_Pack_preview.jpg"
        preview_file.write_bytes(b"dummy_preview_image")

        with patch("task.generate_contact_sheet", return_value=str(preview_file)):
            res = run_build_megapack(payload)
            assert res["status"] == "success"

        cs_subfolder = pack_dir / "Contact Sheets"
        assert cs_subfolder.exists()
        assert (cs_subfolder / "CS_Subdir_Pack_preview.jpg").exists()
