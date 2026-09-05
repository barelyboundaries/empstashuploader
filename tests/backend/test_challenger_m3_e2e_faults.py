"""
Milestone 3 Challenger: Adversarial Coverage Hardening & Fault Injection Test Suite.
Empirical validation of end-to-end user journeys and resilience under extreme fault conditions.
"""

import sys
import os
import io
import time
import json
import tempfile
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import task module under test
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
PLUGIN_DIR = BACKEND_DIR.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import task as task_module
from task import (

    sanitize_name,
    _extract_names,
    _extract_scene_paths,
    check_dependencies,
    emit_progress,
    get_win32_creation_time,
    get_volume_serial_number,
    can_hardlink,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
    is_pid_running,
)

# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path(name: str = "dummy_media.mp4", target_dir=None, pack_title=None):
    if target_dir is not None:
        title_str = str(pack_title) if pack_title is not None else "Megapack"
        target_dir = os.path.join(str(target_dir), sanitize_name(title_str))
        os.makedirs(str(target_dir), exist_ok=True)
        p = os.path.join(str(target_dir), name)
    else:
        global _DUMMY_MEDIA_DIR
        if _DUMMY_MEDIA_DIR is None:
            _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
        p = os.path.join(_DUMMY_MEDIA_DIR, name)
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


class TestAdversarialE2EJourneys:
    """Adversarial stress testing of complete user journeys."""

    def test_e2e_probe_and_build_journey(self, tmp_path):
        """
        Simulates the entire backend lifecycle:
        1. Probe scene files for creation time, hardlinks, duplicate names
        2. Build megapack with contact sheet, torrent, BBCode, manifest
        """
        target_dir = tmp_path / "OutputMegapacks"
        target_dir.mkdir()
        pack_title = "Grand 2026 Collection"
        pack_dir = target_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        # Create simulated scene files
        f1 = tmp_path / "Scene_01_Alpha.mp4"
        f2 = tmp_path / "Scene_02_Beta.mp4"
        f3 = tmp_path / "SubDir" / "Scene_01_Alpha.mp4"
        f3.parent.mkdir()

        f1.write_bytes(b"Simulated video content 1" * 100)
        time.sleep(0.02)
        f2.write_bytes(b"Simulated video content 2" * 200)
        time.sleep(0.02)
        f3.write_bytes(b"Simulated video content 3" * 150)

        # 1. Probe Files
        probe_payload = {
            "target_dir": str(target_dir),
            "files": [
                {"scene_id": 101, "path": str(f1)},
                {"scene_id": 102, "path": str(f2)},
                {"scene_id": 103, "path": str(f3)},
            ]
        }

        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            probe_res = run_probe_files(probe_payload)

        assert probe_res["status"] == "success"
        assert probe_res["task"] == "ProbeFiles"
        assert len(probe_res["files"]) == 3
        # Duplicate basename detection test
        assert probe_res["duplicate_count"] == 1
        assert probe_res["files"][0]["is_duplicate_name"] is True
        assert probe_res["files"][1]["is_duplicate_name"] is False
        assert probe_res["files"][2]["is_duplicate_name"] is True
        assert all(f["exists"] is True for f in probe_res["files"])
        assert all(f["creation_time"] > 0 for f in probe_res["files"])

        # In-place consolidation moves files into pack_dir
        f1_consolidated = pack_dir / "Scene_01_Alpha.mp4"
        f2_consolidated = pack_dir / "Scene_02_Beta.mp4"
        f1_consolidated.write_bytes(f1.read_bytes())
        f2_consolidated.write_bytes(f2.read_bytes())

        # 2. Build Megapack
        build_payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "notes": "Premium quality release",
            "performers": ["Alice Stone", "Bob Rivers"],
            "tags": ["4K", "Feature", "VR"],
            "scenes": [
                {
                    "id": 101,
                    "title": "Scene 1: Introduction",
                    "path": str(f1_consolidated),
                    "performers": ["Alice Stone"],
                    "tags": ["4K"]
                },
                {
                    "id": 102,
                    "title": "Scene 2: Climax",
                    "path": str(f2_consolidated),
                    "performers": ["Bob Rivers"],
                    "tags": ["Feature"]
                }
            ]
        }

        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            build_res = run_build_megapack(build_payload)

        assert build_res["status"] == "success"
        assert build_res["task"] == "BuildMegapack"
        assert build_res["pack_title"] == "Grand 2026 Collection"

        # Verify filesystem artifacts
        torrent_file = Path(build_res["torrent_path"])
        bbcode_file = Path(build_res["bbcode_path"])
        manifest_file = Path(build_res["manifest_path"])

        assert torrent_file.exists()
        assert torrent_file.stat().st_size > 0
        assert bbcode_file.exists()
        assert manifest_file.exists()

        # Check BBCode content
        bbcode_text = bbcode_file.read_text(encoding="utf-8")
        assert "Grand 2026 Collection" in bbcode_text
        assert "Alice Stone" in bbcode_text
        assert "Bob Rivers" in bbcode_text
        assert "Premium quality release" in bbcode_text

        # Check manifest content
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest_data["pack_title"] == "Grand 2026 Collection"
        assert manifest_data["scene_count"] == 2
        assert len(manifest_data["scenes"]) == 2

        # Verify lockfile was automatically cleaned up
        safe_title = sanitize_name("Grand 2026 Collection")
        lockfile = target_dir / f".{safe_title}.lock"
        assert not lockfile.exists()


class TestFaultInjectionLockfiles:
    """Fault injection scenarios for corrupt, stale, and unhandled lockfiles."""

    @pytest.mark.parametrize("corrupt_content,description", [
        ("", "Zero-byte empty lockfile"),
        ("garbage text without pid or timestamp", "Garbage unformatted text"),
        ("pid=not_a_number\nstarted=invalid\n", "Non-numeric pid and started"),
        ("pid=-9999\nstarted=-500\n", "Negative pid"),
        ("started=1700000000\n", "Missing pid line"),
        ("pid=99999999\nstarted=1700000000\n", "Dead non-existent PID"),
        ("pid=1\nstarted=100.0\n", "Expired timestamp older than 1 hour"),
        ("pid=0\nstarted=0.0\n", "Zero PID"),
        ("pack=SomePack\n", "Pack title only"),
    ])
    def test_corrupt_lockfile_auto_recovery(self, tmp_path, corrupt_content, description):
        """Verifies that all corrupted, invalid, and stale lockfiles are automatically reclaimed."""
        output_dir = tmp_path / "Packs"
        output_dir.mkdir()
        pack_title = "Resilient_Pack"
        safe_title = sanitize_name(pack_title)
        lock_file = output_dir / f".{safe_title}.lock"
        lock_file.write_text(corrupt_content, encoding="utf-8")

        build_payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(target_dir=output_dir, pack_title=pack_title)}]
        }

        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            res = run_build_megapack(build_payload)

        assert res["status"] == "success"
        # Ensure lockfile was cleaned up on exit
        assert not lock_file.exists()

    def test_active_concurrent_lockfile_blocking(self, tmp_path):
        """Active lockfile with current running process ID must raise RuntimeError to prevent corruption."""
        output_dir = tmp_path / "Packs"
        output_dir.mkdir()
        pack_title = "Locked_Pack"
        safe_title = sanitize_name(pack_title)
        lock_file = output_dir / f".{safe_title}.lock"
        # Write current process ID and current timestamp (alive and fresh)
        lock_file.write_text(f"pid={os.getpid()}\nstarted={time.time()}\npack={pack_title}\n", encoding="utf-8")

        build_payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(target_dir=output_dir, pack_title=pack_title)}]
        }

        with pytest.raises(RuntimeError) as exc_info:
            run_build_megapack(build_payload)

        assert "Concurrent build in progress" in str(exc_info.value)
        # Lockfile should remain intact
        assert lock_file.exists()

    def test_lockfile_cleanup_on_unhandled_exception(self, tmp_path):
        """Lockfile must always be safely removed in finally block even if an exception occurs during build."""
        output_dir = tmp_path / "Packs"
        output_dir.mkdir()
        pack_title = "Crash_Pack"
        safe_title = sanitize_name(pack_title)
        lock_file = output_dir / f".{safe_title}.lock"

        build_payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(target_dir=output_dir, pack_title=pack_title)}]
        }

        # Mock an exception during emit_progress
        def mock_emit(prog, msg=None):
            if prog == 0.15:
                raise PermissionError("Simulated write permission error")

        with patch.object(task_module, "emit_progress", side_effect=mock_emit):
            with pytest.raises(PermissionError):
                run_build_megapack(build_payload)

        # Lockfile must not linger
        assert not lock_file.exists()


class TestUnicodeAndSpecialCharacterResilience:
    """Stress testing unicode, emojis, illegal Windows filenames, and RTL scripts."""

    @pytest.mark.parametrize("raw_name,expected_prefix", [
        ("メガパック 2026 💖 特選", "メガパック 2026 💖 特選"),
        ("Pack with / \\ : * ? < > | illegal chars", "Pack with _ _ _ _ _ _ _ _ illegal chars"),
        ("CON", "_CON"),
        ("prn.txt", "_prn.txt"),
        ("aux", "_aux"),
        ("NUL.tar.gz", "_NUL.tar.gz"),
        ("COM1", "_COM1"),
        ("LPT9", "_LPT9"),
        ("   ...   leading and trailing dots and spaces ...   ", "leading and trailing dots and spaces"),
        ("A" * 300 + ".mp4", "A" * 103 + ".mp4"),
        ("🎉 Unicode Performer ⭐ (Russian: Привет / Arabic: مرحبا)", "🎉 Unicode Performer ⭐ (Russian_ Привет _ Arabic_ مرحبا)"),
        ("", "Untitled"),
        (None, "Untitled"),
    ])
    def test_sanitize_name_extremes(self, raw_name, expected_prefix):
        """Tests filesystem safety across reserved names, length limits, and illegal characters."""
        sanitized = sanitize_name(raw_name)
        assert sanitized
        assert not any(c in sanitized for c in '<>:/\\|?*\x00\x1f')
        assert len(sanitized) <= 120

    def test_unicode_full_build_journey(self, tmp_path):
        """Full build with complex unicode names, emojis, and Arabic/Japanese characters."""
        output_dir = tmp_path / "UnicodePacks"
        output_dir.mkdir()

        unicode_title = "メガパック 2026 💖 特選 (Collection) [VR+4K] /\\:?*"
        build_payload = {
            "pack_title": unicode_title,
            "output_dir": str(output_dir),
            "notes": "Emoji notes: 🚀✨🎉 and Arabic: مرحبا بالعالم",
            "performers": ["桜木 花道", "Мария Иванова", "Aoi Sora 💖"],
            "tags": ["ハイレゾ", "4K HDR", "40°C 🔥"],
            "scenes": [
                {
                    "id": 999,
                    "title": "Scene 1: 初登場！ 🌟",
                    "performers": ["桜木 花道"],
                    "tags": ["ハイレゾ"],
                    "path": _dummy_media_path(target_dir=output_dir, pack_title=unicode_title)
                }
            ]
        }

        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            res = run_build_megapack(build_payload)

        assert res["status"] == "success"
        safe_title = sanitize_name(unicode_title)
        assert res["torrent_path"].endswith(f"{safe_title}.torrent")
        assert Path(res["torrent_path"]).exists()
        assert Path(res["bbcode_path"]).exists()
        assert Path(res["manifest_path"]).exists()

        # Verify BBCode preserved unicode uncorrupted
        bbcode = Path(res["bbcode_path"]).read_text(encoding="utf-8")
        assert "メガパック 2026 💖 特選" in bbcode
        assert "桜木 花道" in bbcode
        assert "Мария Иванова" in bbcode
        assert "مرحبا بالعالم" in bbcode


class TestEmptyAndNullEdgeCases:
    """Stress testing null, omitted, or empty fields."""

    def test_empty_payload_run_build(self, tmp_path):
        """Building with default pack title."""
        output_dir = tmp_path / "DefaultPacks"
        output_dir.mkdir()
        pack_title = "DefaultPack"

        res = run_build_megapack({
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": _dummy_media_path(target_dir=output_dir, pack_title=pack_title)}],
        })
        assert res["status"] == "success"
        assert res["pack_title"] == pack_title
        assert Path(res["torrent_path"]).exists()
        assert Path(res["bbcode_path"]).exists()
        assert Path(res["manifest_path"]).exists()

    def test_empty_tags_and_performers_fallback(self, tmp_path):
        """Verifies proper fallback aggregation when top-level tags/performers are empty vs in scenes."""
        # Case 1: Top-level empty, scenes have tags/performers -> aggregated from scenes
        output_dir1 = tmp_path / "Packs1"
        output_dir1.mkdir()
        pack_title1 = "Pack_Scene_Aggregation"
        payload1 = {
            "output_dir": str(output_dir1),
            "pack_title": pack_title1,
            "tags": [],
            "performers": [],
            "scenes": [
                {"id": 1, "title": "S1", "performers": ["Performer A"], "tags": ["Tag 1"], "path": _dummy_media_path("scene_a.mp4", target_dir=output_dir1, pack_title=pack_title1)},
                {"id": 2, "title": "S2", "performers": ["Performer B"], "tags": ["Tag 1", "Tag 2"], "path": _dummy_media_path("scene_b.mp4", target_dir=output_dir1, pack_title=pack_title1)},
            ]
        }
        res1 = run_build_megapack(payload1)
        bbcode1 = res1["bbcode"]
        assert "Performer A & Performer B" in bbcode1
        assert "Tag 1, Tag 2" in bbcode1

        # Case 2: Top-level provided -> overrides scenes
        output_dir2 = tmp_path / "Packs2"
        output_dir2.mkdir()
        pack_title2 = "Pack_Top_Override"
        payload2 = {
            "output_dir": str(output_dir2),
            "pack_title": pack_title2,
            "tags": ["ExclusiveTag"],
            "performers": ["Solo Star"],
            "scenes": [
                {"id": 1, "title": "S1", "performers": ["Performer A"], "tags": ["Tag 1"], "path": _dummy_media_path(target_dir=output_dir2, pack_title=pack_title2)},
            ]
        }
        res2 = run_build_megapack(payload2)
        bbcode2 = res2["bbcode"]
        assert "Solo Star" in bbcode2
        assert "ExclusiveTag" in bbcode2
        assert "[b][color=#8a9ba8]Performers[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Performer A" not in bbcode2

        # Case 3: Both top-level and scenes completely empty -> graceful defaults
        output_dir3 = tmp_path / "Packs3"
        output_dir3.mkdir()
        pack_title3 = "Pack_No_Meta"
        payload3 = {
            "output_dir": str(output_dir3),
            "pack_title": pack_title3,
            "tags": [],
            "performers": [],
            "scenes": [
                {"id": 1, "title": "S1", "path": _dummy_media_path(target_dir=output_dir3, pack_title=pack_title3)},
            ]
        }
        res3 = run_build_megapack(payload3)
        bbcode3 = res3["bbcode"]
        assert "Various" in bbcode3
        assert "Megapack" in bbcode3

    def test_extract_names_and_paths_robustness(self):
        """Stress testing _extract_names and _extract_scene_paths across abnormal structures."""
        # Non-collection inputs
        assert _extract_names(None) == []
        assert _extract_names(123) == []
        assert _extract_names(True) == []
        assert _extract_names("Solo Name") == ["Solo Name"]
        assert _extract_names({"name": "Dict Performer"}) == ["Dict Performer"]
        assert _extract_names({"title": "Dict Title"}) == ["Dict Title"]
        assert _extract_names([None, "", "Valid", {"name": "Valid 2"}, {"invalid": "key"}]) == ["Valid", "Valid 2"]

        # Scene path extraction
        assert _extract_scene_paths(None) == []
        assert _extract_scene_paths("C:\\video.mp4") == ["C:\\video.mp4"]
        assert _extract_scene_paths(Path("C:\\video.mp4")) == ["C:\\video.mp4"]
        assert _extract_scene_paths({"path": "C:\\v1.mp4"}) == ["C:\\v1.mp4"]
        assert _extract_scene_paths({"source_path": "C:\\v2.mp4"}) == ["C:\\v2.mp4"]
        assert _extract_scene_paths({"file_paths": ["C:\\v3.mp4", "C:\\v4.mp4"]}) == ["C:\\v3.mp4", "C:\\v4.mp4"]
        assert _extract_scene_paths({"files": [{"path": "C:\\f1.mp4"}, "C:\\f2.mp4"]}) == ["C:\\f1.mp4", "C:\\f2.mp4"]


class TestProgressStreamingAndProtocols:
    """Stress testing stderr progress protocol conformance under invalid/extreme numerical inputs."""

    @pytest.mark.parametrize("input_val,expected_clamped", [
        (0.0, "0.0000"),
        (0.55555, "0.5555"),
        (1.0, "1.0000"),
        (-0.5, "0.0000"),
        (1.5, "1.0000"),
        (float("nan"), "0.0000"),
        (float("inf"), "0.0000"),
        (float("-inf"), "0.0000"),
        ("invalid", "0.0000"),
        (None, "0.0000"),
    ])
    def test_emit_progress_clamping_and_protocol(self, input_val, expected_clamped):
        """Verifies Stash protocol format \x01p\x02<progress_float> and NaN/inf clamping."""
        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            emit_progress(input_val, "Test message")

        out = stderr_buf.getvalue()
        assert f"\x01p\x02{expected_clamped}\n" in out
        assert "Test message" in out


class TestStdinParsingFaultInjection:
    """Fault injection on stdin JSON parsing and arguments structure."""

    def test_stdin_corrupted_json(self):
        """Corrupted JSON string on stdin should log error and return empty payload safely without crash."""
        stdin_buf = io.StringIO("{broken json payload [")
        with patch.object(sys, "stdin", stdin_buf), patch.object(sys.stdin, "isatty", return_value=False):
            stderr_buf = io.StringIO()
            with patch.object(sys, "stderr", stderr_buf):
                mode, payload, server_connection = parse_input_payload()

            assert mode == "build"
            assert payload == {}
            assert server_connection == {}
            assert "Error parsing stdin input" in stderr_buf.getvalue()

    def test_stdin_json_primitives_and_arrays(self):
        """Non-dict JSON roots (strings, lists, numbers) handled gracefully."""
        for invalid_root in ['"just a string"', "[1, 2, 3]", "12345", "true", "null"]:
            stdin_buf = io.StringIO(invalid_root)
            with patch.object(sys, "stdin", stdin_buf), patch.object(sys.stdin, "isatty", return_value=False):
                mode, payload, server_connection = parse_input_payload()
                assert isinstance(payload, dict)

    def test_stdin_stash_plugin_arg_input_list(self):
        """Stash GraphQL args: [PluginArgInput!] format where value is a JSON-encoded string."""
        raw_stash_json = json.dumps({
            "task_name": "BuildMegapack",
            "server_connection": {"Scheme": "http", "Port": 9999},
            "args": [
                {"key": "mode", "value": "build"},
                {"key": "payload", "value": json.dumps({"pack_title": "StashDispatchedPack", "notes": "Dispatched via Stash"})}
            ]
        })
        stdin_buf = io.StringIO(raw_stash_json)
        with patch.object(sys, "stdin", stdin_buf), patch.object(sys.stdin, "isatty", return_value=False):
            mode, payload, server_connection = parse_input_payload()

            assert mode == "build"
            assert server_connection == {"Scheme": "http", "Port": 9999}
            assert payload["pack_title"] == "StashDispatchedPack"
            assert payload["notes"] == "Dispatched via Stash"

    def test_cli_argument_mode_override(self):
        """Command line arguments take effect if specified."""
        with patch.object(sys, "argv", ["task.py", "probe"]):
            stdin_buf = io.StringIO(json.dumps({"files": []}))
            with patch.object(sys, "stdin", stdin_buf), patch.object(sys.stdin, "isatty", return_value=False):
                mode, payload, _ = parse_input_payload()
                assert mode == "probe"


class TestMissingBinariesFaultInjection:
    """Testing behavior when system binaries (ffmpeg, vcsi) are missing."""

    def test_missing_binaries_emits_warning_without_fatal_crash(self):
        """Missing ffmpeg or vcsi emits warning to stderr but does not abort probe/build execution."""
        stderr_buf = io.StringIO()
        with patch("shutil.which", return_value=None):
            with patch.object(sys, "stderr", stderr_buf):
                check_dependencies()

        err_output = stderr_buf.getvalue()
        assert "\x01w\x02Missing binaries:" in err_output
        assert "vcsi" in err_output
        assert "ffmpeg" in err_output
