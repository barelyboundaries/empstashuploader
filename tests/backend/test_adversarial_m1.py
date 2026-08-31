"""
Adversarial stress test suite for Milestone 1:
Backend Task Runner (task.py) & its manifest entrypoint contract.
"""

import sys
import os
import io
import json
import time
import subprocess
import tempfile
from pathlib import Path
import pytest
import torf

# Ensure plugin and backend are in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
PLUGIN_DIR = BACKEND_DIR.parent / "plugin"

if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import task


# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path(target_dir=None, pack_title=None):
    if target_dir is not None:
        if pack_title:
            target_dir = os.path.join(str(target_dir), task.sanitize_name(pack_title))
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
# 1. LOCKFILE CONCURRENCY & STALE RECOVERY
# ============================================================================

class TestLockfileConcurrency:
    """Stress tests for lockfile creation, active process locking, stale recovery, and cleanup."""

    def test_active_pid_lockfile_rejection(self, tmp_path):
        """Verify that an active running process PID in lockfile blocks concurrent execution."""
        output_dir = tmp_path / "lock_test_active"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = task.sanitize_name("Active_Pack")
        lock_file = output_dir / f".{safe_title}.lock"
        
        # Write current running PID
        current_pid = os.getpid()
        lock_file.write_text(f"pid={current_pid}\nstarted={time.time()}\npack=Active_Pack\n", encoding="utf-8")

        payload = {
            "pack_title": "Active_Pack",
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, "Active_Pack")}]
        }

        with pytest.raises(RuntimeError, match="Concurrent build in progress"):
            task.run_build_megapack(payload)
            
        # Lockfile should still exist because active process is holding it
        assert lock_file.exists()

    def test_dead_pid_lockfile_reclaim(self, tmp_path):
        """Verify that a dead PID in lockfile is automatically reclaimed and removed."""
        output_dir = tmp_path / "lock_test_dead"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = task.sanitize_name("Dead_Pack")
        lock_file = output_dir / f".{safe_title}.lock"
        
        # PID 99999999 is definitely not running
        lock_file.write_text("pid=99999999\nstarted=100000.0\npack=Dead_Pack\n", encoding="utf-8")

        payload = {
            "pack_title": "Dead_Pack",
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, "Dead_Pack")}]
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        # After successful build, lockfile must be cleaned up
        assert not lock_file.exists()

    def test_stale_timestamp_lockfile_reclaim(self, tmp_path):
        """Verify that a lockfile older than 3600s is reclaimed even if PID check is ambiguous."""
        output_dir = tmp_path / "lock_test_timeout"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = task.sanitize_name("Timeout_Pack")
        lock_file = output_dir / f".{safe_title}.lock"
        
        # 2 hours old
        old_time = time.time() - 7200
        lock_file.write_text(f"pid=99999999\nstarted={old_time}\npack=Timeout_Pack\n", encoding="utf-8")

        payload = {
            "pack_title": "Timeout_Pack",
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, "Timeout_Pack")}]
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        assert not lock_file.exists()

    def test_is_pid_running_edge_cases(self):
        """Test is_pid_running helper with 0, negative, current, and non-existent PIDs."""
        assert task.is_pid_running(0) is False
        assert task.is_pid_running(-1) is False
        assert task.is_pid_running(-99999) is False
        assert task.is_pid_running(os.getpid()) is True
        assert task.is_pid_running(99999999) is False

    def test_multiprocess_concurrency_race(self, tmp_path):
        """Launch two actual subprocesses simultaneously trying to build the same pack."""
        output_dir = tmp_path / "race_pack"
        output_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "RaceConditionPack"
        pack_dir = output_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)

        dummy_file = pack_dir / "dummy.mp4"
        dummy_file.write_text("video payload")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": str(dummy_file)}]
        }
        input_data = json.dumps({
            "task_name": "BuildMegapack",
            "args": {"mode": "build", "payload": payload}
        })

        cmd = [sys.executable, str(PLUGIN_DIR / "task.py")]

        p1 = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p2 = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        out1, err1 = p1.communicate(input=input_data)
        out2, err2 = p2.communicate(input=input_data)

        codes = [p1.returncode, p2.returncode]
        assert 0 in codes
        safe_title = task.sanitize_name(pack_title)
        lock_file = output_dir / f".{safe_title}.lock"
        assert not lock_file.exists()


# ============================================================================
# 2. MALFORMED, EMPTY, AND CORRUPT STDIN INPUTS
# ============================================================================

class TestStdinRobustness:
    """Stress test parser and main dispatch against corrupt, truncated, and abnormal stdin."""

    def test_empty_stdin(self):
        """Empty stdin should default to mode 'build' with empty payload."""
        sys.stdin = io.StringIO("")
        try:
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload == {}
            assert conn == {}
        finally:
            sys.stdin = sys.__stdin__

    def test_whitespace_stdin(self):
        """Whitespace only stdin."""
        sys.stdin = io.StringIO("   \n\t\r\n   ")
        try:
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload == {}
        finally:
            sys.stdin = sys.__stdin__

    def test_corrupt_json_stdin(self):
        """Corrupt / truncated JSON input should log to stderr and not unhandled crash."""
        sys.stdin = io.StringIO("{broken json: [1, 2,")
        stderr_capture = io.StringIO()
        sys.stderr = stderr_capture
        try:
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload == {}
            assert "Error parsing stdin input" in stderr_capture.getvalue()
        finally:
            sys.stdin = sys.__stdin__
            sys.stderr = sys.__stderr__

    def test_json_non_dict_root(self):
        """JSON roots that are arrays, ints, strings, or booleans."""
        for non_dict in ["[1, 2, 3]", '"string_root"', "12345", "true", "null"]:
            sys.stdin = io.StringIO(non_dict)
            try:
                mode, payload, conn = task.parse_input_payload()
                assert mode == "build"
                assert payload == {}
            finally:
                sys.stdin = sys.__stdin__

    def test_args_as_list_with_mixed_elements(self):
        """Args list containing invalid / non-dict elements alongside valid ones."""
        test_payload = {
            "task_name": "ProbeFiles",
            "args": [
                None,
                "not a dict",
                123,
                {"key": "mode", "value": "probe"},
                {"key": "payload", "value": json.dumps({"target_dir": "C:/Packs"})},
                {"key": "custom_extra", "value": "extra_val"}
            ]
        }
        sys.stdin = io.StringIO(json.dumps(test_payload))
        try:
            mode, payload, conn = task.parse_input_payload()
            assert mode == "probe"
            assert payload.get("target_dir") == "C:/Packs"
            assert payload.get("custom_extra") == "extra_val"
        finally:
            sys.stdin = sys.__stdin__

    def test_args_as_raw_json_string(self):
        """Args provided directly as a JSON-encoded string."""
        inner_payload = {"target_dir": "D:/DirectPayload", "files": []}
        test_payload = {
            "task_name": "ProbeFiles",
            "args": json.dumps(inner_payload)
        }
        sys.stdin = io.StringIO(json.dumps(test_payload))
        try:
            mode, payload, conn = task.parse_input_payload()
            assert mode == "probe"
            assert payload.get("target_dir") == "D:/DirectPayload"
        finally:
            sys.stdin = sys.__stdin__


# ============================================================================
# 3. UNICODE, EMOJI, QUOTES, AND SPECIAL CHARACTERS
# ============================================================================

class TestUnicodeAndSpecialCharacters:
    """Stress tests for unicode titles, emojis, quotes, windows reserved names, and character limits."""

    def test_sanitize_name_windows_reserved_device_names(self):
        """Windows reserved device names must be prefixed to avoid OS file errors."""
        for dev in ["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9", "con", "nul", "aux"]:
            sanitized = task.sanitize_name(dev)
            assert not sanitized.upper().startswith(dev.upper() + "."), f"Reserved {dev} should be escaped"
            assert sanitized.startswith("_")

    def test_sanitize_name_illegal_characters(self):
        """Characters forbidden on Windows filesystem: < > : \" / \\ | ? * and control chars."""
        illegal_input = 'A < B > C : D " E / F \\ G | H ? I * J \x00 K \x1f L'
        sanitized = task.sanitize_name(illegal_input)
        for char in '<>:"/\\|?*\x00\x1f':
            assert char not in sanitized

    def test_sanitize_name_dots_and_spaces_stripping(self):
        """Trailing dots and spaces are illegal in Windows folder and file names."""
        assert task.sanitize_name("  ...  pack name ...   ") == "pack name"
        assert task.sanitize_name("...") == "Untitled"
        assert task.sanitize_name("     ") == "Untitled"
        assert task.sanitize_name("") == "Untitled"

    def test_sanitize_name_unicode_and_emojis(self):
        """Unicode characters (Kanji, Cyrillic, Greek, Arabic, Emoji) must be preserved."""
        unicode_name = "🎬 鈴木 一朗 & Александра ロマノフ — 2026 ⭐"
        sanitized = task.sanitize_name(unicode_name)
        assert "鈴木 一朗" in sanitized
        assert "Александра" in sanitized
        assert "🎬" in sanitized
        assert "⭐" in sanitized

    def test_extract_names_variations(self):
        """Test _extract_names with strings, dicts, emojis, duplicates, empty elements."""
        raw_items = [
            "  Alice 🐱  ",
            {"name": "Bob 🐶"},
            {"title": "Charlie 🌟"},
            {"other": "Ignored"},
            "",
            "   ",
            "Alice 🐱",
            {"name": "Bob 🐶"},
            {"name": ""},
            {"title": None},
        ]
        extracted = task._extract_names(raw_items)
        assert extracted == ["Alice 🐱", "Bob 🐶", "Charlie 🌟"]

    def test_extract_names_single_string(self):
        """_extract_names passed a single string instead of a list."""
        assert task._extract_names("Single Performer 🚀") == ["Single Performer 🚀"]
        assert task._extract_names("   ") == []
        assert task._extract_names(None) == []

    def test_extract_scene_paths_variations(self):
        """_extract_scene_paths with various scene dict structures."""
        s1 = {"file_paths": ["C:/video1.mp4", "", None, "C:/video2.mp4"]}
        assert task._extract_scene_paths(s1) == ["C:/video1.mp4", "C:/video2.mp4"]

        s2 = {"path": "C:/video3.mp4"}
        assert task._extract_scene_paths(s2) == ["C:/video3.mp4"]

        s3 = {"source_path": "C:/video4.mp4"}
        assert task._extract_scene_paths(s3) == ["C:/video4.mp4"]

        s4 = {"files": [{"path": "C:/video5.mp4"}, "C:/video6.mp4", {"path": ""}]}
        assert task._extract_scene_paths(s4) == ["C:/video5.mp4", "C:/video6.mp4"]

    def test_probe_files_with_unicode_and_case_collisions(self, tmp_path):
        """Filesystem probe with Unicode file names and Windows case-insensitive collision detection."""
        f_utf8 = tmp_path / "🎬_東京_scene.mp4"
        f_utf8.write_text("japanese video", encoding="utf-8")

        f_upper = tmp_path / "DUPLICATE_CASE.MP4"
        f_upper.write_text("upper")
        f_lower = tmp_path / "duplicate_case.mp4"

        payload = {
            "target_dir": str(tmp_path),
            "files": [
                {"id": 1, "path": str(f_utf8)},
                {"id": 2, "path": str(f_upper)},
                {"id": 3, "path": str(f_lower)},
            ]
        }

        result = task.run_probe_files(payload)
        assert result["status"] == "success"
        assert len(result["files"]) == 3
        assert result["files"][0]["exists"] is True
        assert result["files"][0]["basename"] == "🎬_東京_scene.mp4"
        assert result["files"][0]["is_duplicate_name"] is False

        # Windows duplicate case check
        assert result["files"][1]["is_duplicate_name"] is True
        assert result["files"][2]["is_duplicate_name"] is True
        assert result["duplicate_count"] == 1

    def test_build_megapack_unicode_and_quotes(self, tmp_path):
        """BuildMegapack with full Unicode pack title, Russian performers, emojis, quotes."""
        output_dir = tmp_path / "unicode_pack_out"
        output_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "🎬 Megapack: Éléonore & 鈴木 / \"Special\" [2026] <Pack> * Final?"
        pack_dir = output_dir / task.sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        video_file = pack_dir / "unicode_video.mp4"
        video_file.write_text("video dummy")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "performers": ["Éléonore François", "鈴木 一朗", "Александра Романова"],
            "tags": ["4K 60fps", "J-Pop 🌸", "Special \"Edition\""],
            "notes": "Testing quotes: \"double\" and 'single' and \nnewlines.",
            "include_contact_sheets": False,
            "scenes": [
                {
                    "id": 501,
                    "title": "Scene 1: \"Intro\"",
                    "path": str(video_file),
                }
            ]
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        assert result["pack_title"] == pack_title
        assert os.path.exists(result["torrent_path"])
        assert os.path.exists(result["manifest_path"])
        assert os.path.exists(result["bbcode_path"])

        # Check bbcode content preserves unicode and quotes
        bbcode = result["bbcode"]
        assert "Éléonore François" in bbcode
        assert "鈴木 一朗" in bbcode
        assert "Александра Романова" in bbcode
        assert "J-Pop 🌸" in bbcode
        assert "\"double\"" in bbcode

        # Check manifest JSON
        with open(result["manifest_path"], "r", encoding="utf-8") as mf:
            manifest_data = json.load(mf)
            assert manifest_data["pack_title"] == pack_title
            assert manifest_data["scene_count"] == 1

    def test_torrent_bencode_unicode_byte_length_integrity(self, tmp_path):
        """Test whether torrent file generated with multi-byte unicode title is valid bencode."""
        pack_title = "鈴木_Pack_🌸"
        output_dir = tmp_path / pack_title
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(output_dir)}]
        }

        result = task.run_build_megapack(payload)
        torrent_path = result["torrent_path"]
        assert os.path.exists(torrent_path)

        with open(torrent_path, "rb") as f:
            torrent_bytes = f.read()

        safe_title = task.sanitize_name(pack_title)
        name_bytes = safe_title.encode("utf-8")
        assert str(len(name_bytes)).encode("ascii") + b":" + name_bytes in torrent_bytes, (
            f"Bencode length prefix mismatch! Title was length-prefixed by characters "
            f"instead of bytes: {torrent_bytes[:60]}"
        )

        t = torf.Torrent.read(torrent_path)
        assert t.name == safe_title
        assert t.pieces > 0, "torrent must carry real piece hashes"


# ============================================================================
# 4. PROGRESS STREAMING PROTOCOL CONFORMANCE
# ============================================================================

class TestProgressProtocolConformance:
    """Stress tests for \\x01p\\x02<float> protocol format, clamping, NaN handling, and stream separation."""

    def test_progress_protocol_exact_format(self):
        """Verify \\x01p\\x02<float> format is strictly emitted on stderr."""
        stderr_capture = io.StringIO()
        sys.stderr = stderr_capture
        try:
            task.emit_progress(0.75, "Three quarters done")
            val = stderr_capture.getvalue()
            lines = val.splitlines()
            assert lines[0] == "\x01p\x020.7500"
            assert lines[1] == "\x01i\x02[75%] Three quarters done"
        finally:
            sys.stderr = sys.__stderr__

    def test_progress_clamping_and_nan_safety(self):
        """Verify out-of-range, NaN, and Inf progress values do not crash or emit invalid floats."""
        for bad_val, expected_str in [
            (-100.0, "\x01p\x020.0000"),
            (999.0, "\x01p\x021.0000"),
            (float("nan"), "\x01p\x020.0000"),
            (float("inf"), "\x01p\x020.0000"),
            (float("-inf"), "\x01p\x020.0000"),
            ("invalid_str", "\x01p\x020.0000"),
            (None, "\x01p\x020.0000"),
        ]:
            stderr_capture = io.StringIO()
            sys.stderr = stderr_capture
            try:
                task.emit_progress(bad_val)
                assert expected_str in stderr_capture.getvalue()
            finally:
                sys.stderr = sys.__stderr__

    def test_stdout_stderr_clean_separation(self, tmp_path):
        """Execute task.py as a real subprocess and verify stdout is pure JSON without stderr progress noise."""
        payload = {
            "target_dir": str(tmp_path),
            "files": []
        }
        input_data = json.dumps({
            "task_name": "ProbeFiles",
            "args": {"mode": "probe", "payload": payload}
        })

        proc = subprocess.Popen(
            [sys.executable, str(PLUGIN_DIR / "task.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )
        stdout, stderr = proc.communicate(input=input_data)

        assert proc.returncode == 0
        
        # Stderr must contain progress codes
        assert "\x01p\x02" in stderr
        
        # Stdout must NOT contain progress codes
        assert "\x01p\x02" not in stdout
        assert "\x01w\x02" not in stdout
        assert "\x01e\x02" not in stdout

        # Stdout must parse cleanly as JSON
        parsed_out = json.loads(stdout)
        assert parsed_out["status"] == "success"
        assert parsed_out["task"] == "ProbeFiles"


# ============================================================================
# 5. MANIFEST ENTRYPOINT CONTRACT (deepseek-megapack.yml exec + task.py)
# ============================================================================

def _manifest_exec(yml_path):
    """Parse the top-level `exec:` list from deepseek-megapack.yml.

    Tiny block parser (no pyyaml dependency): the exec block is the list of
    `  - "..."` items immediately following the `exec:` key, ending at the
    next non-item line.
    """
    exec_items = []
    in_exec = False
    for raw in yml_path.read_text(encoding="utf-8").splitlines():
        if raw.rstrip() == "exec:":
            in_exec = True
            continue
        if in_exec:
            if raw.startswith("  - "):
                exec_items.append(raw[4:].strip().strip('"'))
            elif raw.strip():
                break
    return exec_items


class TestDispatcherExecution:
    """The manifest entrypoint contract that replaced the legacy cmd/bat dispatcher.

    The Windows-only batch launcher was deliberately removed from the
    distribution: the manifest must exec plain `python` against this plugin's
    task.py (cross-platform), and task.py's own bootstrap resolves the
    interpreter and dependencies from there.
    """

    def test_manifest_exec_is_python_task_py(self):
        """deepseek-megapack.yml exec is exactly ["python", "{pluginDir}/task.py"]."""
        yml_path = PLUGIN_DIR / "deepseek-megapack.yml"
        assert yml_path.exists(), f"missing plugin manifest: {yml_path}"
        exec_value = _manifest_exec(yml_path)
        assert exec_value == ["python", "{pluginDir}/task.py"]

    def test_manifest_exec_target_exists(self):
        """The exec target resolves to a real file beside the manifest (plugin/task.py)."""
        yml_path = PLUGIN_DIR / "deepseek-megapack.yml"
        exec_value = _manifest_exec(yml_path)
        target = exec_value[-1]
        assert target.startswith("{pluginDir}/"), (
            f"exec target must be plugin-relative: {target!r}"
        )
        entry = PLUGIN_DIR / target[len("{pluginDir}/"):]
        assert entry.is_file(), f"manifest exec target does not exist: {entry}"
