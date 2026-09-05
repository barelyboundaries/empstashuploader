"""
Milestone 3 — Tier 5 Adversarial Coverage & Stress Test Suite
Empirical Challenger 1 Suite covering task runner, probing, locking, torrents,
metadata, oshash, image size enforcement, and CLI streaming protocol.
"""

import os
import sys
import io
import json
import math
import time
import shutil
import tempfile
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from plugin.task import (
    sanitize_name,
    _extract_names,
    _extract_scene_paths,
    check_dependencies,
    emit_progress,
    get_win32_creation_time,
    get_volume_serial_number,
    can_hardlink,
    run_probe_files,
    is_pid_running,
    run_build_megapack,
    parse_input_payload,
)
from empornium_megapack.paths import oshash_file, PathMapper, verify_same_file
from empornium_megapack.metadata import (
    resolution_for,
    format_duration,
    join_names,
    empify,
    bbcode_escape,
    merge_tags,
    pack_performer_union,
    pack_studio,
    pack_title_default,
    scene_title_default,
    render_description,
    finalize_description,
    normalize_meta_input,
    ImagePlaceholderError,
)
from empornium_megapack.torrents import (
    piece_size_for,
    source_for_announce,
    validate_announce_url,
    sanitize_announce_url,
    create_torrent,
    TorrentError,
    MIN_PIECE_SIZE,
    MAX_PIECE_EXPONENT,
)
from empornium_megapack.images import (
    sha256_file,
    enforce_size_limit,
    _retry_delay,
    _retry_after_seconds,
    ContactSheetError,
)
from empornium_megapack.build import unique_names, make_bundle, stage_payload, BuildError
from empornium_megapack.config import Settings



# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path(target_dir=None, pack_title=None):
    if target_dir is not None:
        title_str = str(pack_title) if pack_title is not None else "Megapack"
        target_dir = os.path.join(str(target_dir), sanitize_name(title_str))
        os.makedirs(str(target_dir), exist_ok=True)
        p = os.path.join(str(target_dir), "dummy_media.mp4")
    else:
        global _DUMMY_MEDIA_DIR
        if _DUMMY_MEDIA_DIR is None:
            _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
        p = os.path.join(_DUMMY_MEDIA_DIR, "dummy_media.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


# ============================================================================
# 1. TASK RUNNER STDIN PARSING & PAYLOAD EXTRACTION ADVERSARIAL TESTS
# ============================================================================

class TestTaskRunnerStdinParsing:
    """Stress-test parse_input_payload with extreme, malformed, and boundary inputs."""

    def test_parse_empty_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: "")
        mode, payload, conn = parse_input_payload()
        assert mode == "build"
        assert payload == {}
        assert conn == {}

    def test_parse_whitespace_only_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: "   \n\t  \r\n  ")
        mode, payload, conn = parse_input_payload()
        assert mode == "build"
        assert payload == {}
        assert conn == {}

    def test_parse_invalid_json_garbage(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: "<<<NOT_JSON_AT_ALL>>>")
        mode, payload, conn = parse_input_payload()
        assert mode == "build"
        assert payload == {}
        assert conn == {}

    def test_parse_primitive_json_roots(self, monkeypatch):
        for raw in ["12345", '"hello"', "true", "false", "null", "[1, 2, 3]"]:
            monkeypatch.setattr(sys, "argv", ["task.py"])
            monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
            monkeypatch.setattr(sys.stdin, "read", lambda r=raw: r)
            mode, payload, conn = parse_input_payload()
            assert mode == "build"
            assert isinstance(payload, dict)
            assert isinstance(conn, dict)

    def test_parse_task_name_probe(self, monkeypatch):
        data = {
            "task_name": "ProbeFiles",
            "server_connection": {"host": "localhost", "port": 9999},
            "args": [{"key": "mode", "value": "probe"}, {"key": "payload", "value": '{"target_dir": "C:\\\\Packs"}'}],
        }
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(data))
        mode, payload, conn = parse_input_payload()
        assert mode == "probe"
        assert payload == {"target_dir": "C:\\Packs"}
        assert conn == {"host": "localhost", "port": 9999}

    def test_parse_args_as_dict_with_string_payload(self, monkeypatch):
        data = {
            "task_name": "BuildMegapack",
            "args": {
                "mode": "build",
                "payload": json.dumps({"pack_title": "Test Pack", "scenes": [{"id": 1, "path": _dummy_media_path()}]}),
            }
        }
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(data))
        mode, payload, conn = parse_input_payload()
        assert mode == "build"
        assert payload.get("pack_title") == "Test Pack"

    def test_parse_args_as_raw_json_string(self, monkeypatch):
        data = {
            "task_name": "ProbeFiles",
            "args": json.dumps({"target_dir": "D:\\Stash", "files": ["f1.mp4"]}),
        }
        monkeypatch.setattr(sys, "argv", ["task.py"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdin, "read", lambda: json.dumps(data))
        mode, payload, conn = parse_input_payload()
        assert mode == "probe"
        assert payload.get("target_dir") == "D:\\Stash"

    def test_parse_argv_mode_override(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["task.py", "probe"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        mode, payload, conn = parse_input_payload()
        assert mode == "probe"


# ============================================================================
# 2. EMIT PROGRESS PROTOCOL ADVERSARIAL TESTS
# ============================================================================

class TestEmitProgress:
    """Verify Stash native stderr progress protocol formatting and clamping."""

    def test_emit_progress_clamping_and_non_finite(self, monkeypatch):
        stderr_buf = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stderr_buf)

        # Clamping below 0
        emit_progress(-0.5, "Underflow")
        assert "\x01p\x020.0000\n" in stderr_buf.getvalue()

        stderr_buf.seek(0)
        stderr_buf.truncate(0)

        # Clamping above 1
        emit_progress(2.5, "Overflow")
        assert "\x01p\x021.0000\n" in stderr_buf.getvalue()

        stderr_buf.seek(0)
        stderr_buf.truncate(0)

        # Non-finite values
        emit_progress(float("nan"), "NaN value")
        assert "\x01p\x020.0000\n" in stderr_buf.getvalue()

        stderr_buf.seek(0)
        stderr_buf.truncate(0)

        emit_progress(float("inf"), "Inf value")
        assert "\x01p\x020.0000\n" in stderr_buf.getvalue()

        stderr_buf.seek(0)
        stderr_buf.truncate(0)

        # Invalid type string
        emit_progress("invalid_float", "Bad type")
        assert "\x01p\x020.0000\n" in stderr_buf.getvalue()


# ============================================================================
# 3. WIN32 CREATION TIME & VOLUME SERIAL NUMBER PROBING
# ============================================================================

class TestWin32Probing:
    """Stress-test Win32 creation time and volume serial number resolution."""

    def test_win32_creation_time_real_file(self, tmp_path):
        f = tmp_path / "test_probe.mp4"
        f.write_bytes(b"content")
        ctime = get_win32_creation_time(str(f))
        assert isinstance(ctime, float)
        assert ctime > 0
        assert abs(ctime - time.time()) < 10.0

    def test_win32_creation_time_nonexistent_and_invalid(self):
        assert get_win32_creation_time("C:\\nonexistent_dummy_path_123.mp4") == 0.0
        assert get_win32_creation_time("") == 0.0
        assert get_win32_creation_time(None) == 0.0
        assert get_win32_creation_time(12345) == 0.0

    def test_volume_serial_number_root(self):
        c_serial = get_volume_serial_number("C:\\")
        if os.name == "nt":
            assert c_serial is not None
            assert isinstance(c_serial, int)

    def test_volume_serial_invalid_inputs(self):
        assert get_volume_serial_number("") is None
        assert get_volume_serial_number(None) is None
        assert get_volume_serial_number(9999) is None
        # Q:\ is unmounted on this system and must return None
        if not os.path.exists("Q:\\"):
            assert get_volume_serial_number("Q:\\unmounted_drive_9999\\") is None

    def test_can_hardlink_same_volume(self, tmp_path):
        f1 = tmp_path / "f1.mp4"
        f1.write_bytes(b"data")
        assert can_hardlink(str(f1), str(tmp_path)) is True

    def test_can_hardlink_empty_inputs(self):
        assert can_hardlink("", "C:\\temp") is False
        assert can_hardlink("C:\\temp\\f.mp4", "") is False
        assert can_hardlink(None, None) is False


# ============================================================================
# 4. RUN PROBE FILES EDGE CASES & DUPLICATE DETECTION
# ============================================================================

class TestRunProbeFiles:
    """Test run_probe_files across heterogeneous scene formats and duplicates."""

    def test_probe_files_comprehensive(self, tmp_path):
        f1 = tmp_path / "Scene_A.mp4"
        f1.write_bytes(b"Scene A data")
        f2 = tmp_path / "Scene_B.mp4"
        f2.write_bytes(b"Scene B data")

        # Subdir duplicate of Scene_A with different case
        sub = tmp_path / "sub"
        sub.mkdir()
        f1_dup = sub / "SCENE_A.MP4"
        f1_dup.write_bytes(b"Duplicate A")

        payload = {
            "target_dir": str(tmp_path),
            "files": [
                str(f1),
                {"scene_id": 10, "path": str(f1_dup)},
                {"scene_id": 20, "source_path": str(f2)},
                {"scene_id": 30, "file_paths": [str(f1)]},
                {"scene_id": 40, "files": [{"path": str(f2)}]},
                {"scene_id": 99, "path": str(tmp_path / "missing.mp4")},
            ],
        }

        result = run_probe_files(payload)
        assert result["status"] == "success"
        assert len(result["files"]) == 6
        assert result["duplicate_count"] == 2  # Scene_A and Scene_B are both duplicated

        files = result["files"]
        assert files[0]["exists"] is True
        assert files[0]["is_duplicate_name"] is True
        assert files[1]["exists"] is True
        assert files[1]["is_duplicate_name"] is True
        assert files[2]["exists"] is True
        assert files[2]["is_duplicate_name"] is True
        assert files[5]["exists"] is False
        assert files[5]["size"] == 0

    def test_probe_files_empty_payload(self):
        result = run_probe_files({})
        assert result["status"] == "success"
        assert result["files"] == []
        assert result["duplicate_count"] == 0

        result_null = run_probe_files(None)
        assert result_null["status"] == "success"
        assert result_null["files"] == []


# ============================================================================
# 5. CONCURRENT BUILD PROTECTION & LOCKFILE RECLAIM
# ============================================================================

class TestLockfileAndConcurrency:
    """Stress-test concurrent build protection, lockfile stale recovery, and cleanup."""

    def test_is_pid_running(self):
        my_pid = os.getpid()
        assert is_pid_running(my_pid) is True
        assert is_pid_running(0) is False
        assert is_pid_running(-1) is False
        assert is_pid_running(99999999) is False

    def test_stale_lockfile_recovery_dead_pid(self, tmp_path):
        out_dir = tmp_path / "packs"
        out_dir.mkdir()
        lock = out_dir / ".Test_Pack.lock"
        # Write dead PID
        lock.write_text("pid=99999999\nstarted=1000000.0\npack=Test_Pack\n", encoding="utf-8")

        payload = {
            "pack_title": "Test_Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, "Test_Pack")}],
        }

        res = run_build_megapack(payload)
        assert res["status"] == "success"
        # Lockfile must be cleanly removed after build
        assert not lock.exists()

    def test_stale_lockfile_recovery_corrupted_content(self, tmp_path):
        out_dir = tmp_path / "packs"
        out_dir.mkdir()
        lock = out_dir / ".Corrupted_Pack.lock"
        lock.write_text("CORRUPTED_GARBAGE_CONTENT_NO_PID\n", encoding="utf-8")

        payload = {
            "pack_title": "Corrupted_Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, "Corrupted_Pack")}],
        }

        res = run_build_megapack(payload)
        assert res["status"] == "success"
        assert not lock.exists()

    def test_concurrent_build_rejected_when_live_pid(self, tmp_path):
        out_dir = tmp_path / "packs"
        out_dir.mkdir()
        lock = out_dir / ".Live_Pack.lock"
        # Write current process PID with fresh timestamp
        lock.write_text(f"pid={os.getpid()}\nstarted={time.time()}\npack=Live_Pack\n", encoding="utf-8")

        payload = {
            "pack_title": "Live_Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, "Live_Pack")}],
        }

        with pytest.raises(RuntimeError, match="Concurrent build in progress"):
            run_build_megapack(payload)


# ============================================================================
# 6. BUILD MEGAPACK ARTIFACT GENERATION & EXTRACTORS
# ============================================================================

class TestBuildMegapackArtifacts:
    """Verify generated artifacts: .torrent, manifest.json, and BBCode."""

    def test_build_megapack_end_to_end(self, tmp_path):
        out_dir = tmp_path / "megapacks"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "Adversarial Test Megapack"
        pack_dir = out_dir / sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        f1 = pack_dir / "video1.mp4"
        f1.write_bytes(b"Video 1 dummy data")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "notes": "Special edition with [tags] and quotes!",
            "performers": ["Alice Wonder", "Bob Builder"],
            "tags": ["1080p", "Featured"],
            "scenes": [
                {
                    "id": 1,
                    "title": "Scene 1",
                    "path": str(f1),
                    "performers": [{"name": "Alice Wonder"}],
                    "tags": [{"name": "1080p"}],
                }
            ],
        }

        res = run_build_megapack(payload)
        assert res["status"] == "success"
        assert os.path.isfile(res["torrent_path"])
        assert os.path.isfile(res["manifest_path"])
        assert os.path.isfile(res["bbcode_path"])

        # Validate manifest contents
        with open(res["manifest_path"], "r", encoding="utf-8") as mf:
            manifest = json.load(mf)
        assert manifest["pack_title"] == "Adversarial Test Megapack"
        assert manifest["scene_count"] == 1
        assert len(manifest["contact_sheets"]) > 0

        # Validate BBCode contents
        bbcode = res["bbcode"]
        assert "Adversarial Test Megapack" in bbcode
        assert "Alice Wonder & Bob Builder" in bbcode
        # Notes render in the panel's own painted block, not the skin's [quote]
        assert "[color=#f5f8fa]Special edition with &#91;tags&#93; and quotes![/color]" in bbcode

    def test_extract_names_heterogeneous_inputs(self):
        assert _extract_names(None) == []
        assert _extract_names("") == []
        assert _extract_names("Single Performer") == ["Single Performer"]
        assert _extract_names(["A", "B", "A", ""]) == ["A", "B"]
        assert _extract_names([{"name": "Alice"}, {"title": "Bob"}, {"name": "Alice"}]) == ["Alice", "Bob"]
        assert _extract_names({"name": "Single Dict"}) == ["Single Dict"]

    def test_extract_scene_paths_heterogeneous_inputs(self):
        assert _extract_scene_paths(None) == []
        assert _extract_scene_paths("C:\\path.mp4") == ["C:\\path.mp4"]
        assert _extract_scene_paths({"file_paths": ["a.mp4", "b.mp4"]}) == ["a.mp4", "b.mp4"]
        assert _extract_scene_paths({"source_path": "c.mp4"}) == ["c.mp4"]
        assert _extract_scene_paths({"files": [{"path": "d.mp4"}, "e.mp4"]}) == ["d.mp4", "e.mp4"]


# ============================================================================
# 7. METADATA, OSHASH, TORRENTS, & IMAGES ADVERSARIAL EDGE CASES
# ============================================================================

class TestMetadataAndOshash:
    """Stress-test oshash, metadata ladder, BBCode escaping, and piece size calculation."""

    def test_oshash_under_8_bytes_raises(self, tmp_path):
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"1234567")
        with pytest.raises(ValueError, match="8 bytes or fewer"):
            oshash_file(f)

    def test_oshash_deterministic_calculation(self, tmp_path):
        f = tmp_path / "exact_64k.bin"
        f.write_bytes(b"A" * 65536)
        h1 = oshash_file(f)
        h2 = oshash_file(f)
        assert len(h1) == 16
        assert h1 == h2

    def test_bbcode_escape_special_chars(self):
        assert bbcode_escape("Hello [World] 123") == "Hello &#91;World&#93; 123"
        assert bbcode_escape("Control \x00\x08 characters") == "Control characters"
        assert bbcode_escape("New\nlines\r\nhere", keep_newlines=True) == "New\nlines\r\nhere"

    def test_empify_tags(self):
        assert empify("  Big_Tits.1080p!!  ") == "big.tits.1080p"
        assert empify("A" * 50) == "a" * 32

    def test_resolution_for_boundaries(self):
        assert resolution_for(None) == ""
        assert resolution_for(0) == ""
        assert resolution_for(143) == ""
        assert resolution_for(144) == "144p"
        assert resolution_for(720) == "720p"
        assert resolution_for(1080) == "1080p"
        assert resolution_for(1920) == "2160p"
        assert resolution_for(4000) == "8K"
        assert resolution_for(7000) == "8K+"

    def test_format_duration_boundaries(self):
        assert format_duration(None) == ""
        assert format_duration(0) == ""
        assert format_duration(-10) == ""
        assert format_duration(45) == "0:45"
        assert format_duration(65) == "1:05"
        assert format_duration(3665) == "1:01:05"

    def test_piece_size_for_boundaries(self):
        assert piece_size_for(0) == 16384
        assert piece_size_for(1024) == 16384
        assert piece_size_for(1024 * 1024 * 1024) == 1048576  # 1 GiB -> 1 MiB
        assert piece_size_for(1024 * 1024 * 1024 * 100) == 8388608  # 100 GiB -> capped at 8 MiB

    def test_source_for_announce(self):
        assert source_for_announce("http://tracker.empornium.sx:2710/token/announce") == "Emp"
        assert source_for_announce("http://enthralled.me/announce") == "Ent"
        assert source_for_announce("https://femdomcult.org/announce") == "FDC"
        assert source_for_announce("http://happyfappy.org/announce") == "HF"
        assert source_for_announce("http://kufirc.com/announce") == "Kufirc"
        assert source_for_announce("http://pornbay.org/announce") == "PBay"
        assert source_for_announce("http://unknown-tracker.com/announce") == "unknown-tracker.com"

    def test_validate_announce_url_failures(self):
        with pytest.raises(TorrentError, match="http or https"):
            validate_announce_url("udp://tracker.openbittorrent.com:80/announce")
        with pytest.raises(TorrentError, match="no host"):
            validate_announce_url("http:///announce")
        with pytest.raises(TorrentError, match="announce endpoint"):
            validate_announce_url("http://tracker.empornium.sx/download")

    def test_sanitize_announce_url_masking(self):
        url1 = "http://tracker.empornium.sx:2710/token1/token2/announce"
        masked1 = sanitize_announce_url(url1)
        assert "token1" not in masked1
        assert "token2" not in masked1
        assert "xxxxxx/xxxxxx/announce" in masked1

        url2 = "http://tracker.empornium.sx:2710/announce?passkey=0123456789abcdef0123456789abcdef"
        masked2 = sanitize_announce_url(url2)
        assert "0123456789abcdef" not in masked2
        assert "passkey=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" in masked2


# ============================================================================
# 8. SUBPROCESS CLI RUNNER TEST
# ============================================================================

class TestSubprocessCliRunner:
    """Execute plugin/task.py as a real subprocess via stdin."""

    def test_subprocess_cli_probe(self, tmp_path):
        f = tmp_path / "sub_test.mp4"
        f.write_bytes(b"video payload")

        payload = {
            "task_name": "ProbeFiles",
            "args": [
                {"key": "mode", "value": "probe"},
                {"key": "payload", "value": json.dumps({"target_dir": str(tmp_path), "files": [str(f)]})},
            ],
        }

        task_py = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"
        proc = subprocess.run(
            [sys.executable, str(task_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert proc.returncode == 0
        assert "\x01p\x02" in proc.stderr
        out = json.loads(proc.stdout)
        assert out["status"] == "success"
        assert out["files"][0]["exists"] is True

    def test_subprocess_cli_build(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_name": "BuildMegapack",
            "args": [
                {"key": "mode", "value": "build"},
                {"key": "payload", "value": json.dumps({"pack_title": "CLI Pack", "output_dir": str(out_dir), "scenes": [{"id": 1, "path": _dummy_media_path(out_dir, "CLI Pack")}]})},
            ],
        }

        task_py = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"
        proc = subprocess.run(
            [sys.executable, str(task_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert proc.returncode == 0
        assert "\x01p\x02" in proc.stderr
        out = json.loads(proc.stdout)
        assert out["status"] == "success"
        assert os.path.isfile(out["torrent_path"])
        assert os.path.isfile(out["manifest_path"])
