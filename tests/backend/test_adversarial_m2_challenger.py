"""
DeepSeek Megapack Milestone 2 — Adversarial Challenger 2 Test Suite.
Rigorous stress-testing of fallback mechanisms (VCSI failure, upload failure, timeouts,
Pillow placeholder generation) and payload validation (empty, null, corrupt JSON,
missing files, preventing hollow torrents and partial manifest artifacts).
"""

import io
import json
import os
import sys
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image, ImageStat
import torf

# Ensure local venv and plugin directory are on sys.path
PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task
from deepseek_megapack.config import get_settings
from deepseek_megapack.torrents import create_torrent, TorrentError
from deepseek_megapack.images import generate_contact_sheet as domain_generate_cs, ContactSheetError
from deepseek_megapack.build import sanitize_name, write_manifest


# ---------------------------------------------------------------------------
# Helper: Synthetic Video Fixture
# ---------------------------------------------------------------------------
def make_synthetic_video(path: str | Path, duration: int = 4) -> str:
    """Creates a small valid h264 mp4 test video via ffmpeg if available, or dummy bytes."""
    path_str = str(path)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg, "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=320x240:rate=15",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(duration), path_str
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and os.path.exists(path_str) and os.path.getsize(path_str) > 0:
            return path_str

    # Fallback to dummy binary file for torrent testing
    with open(path_str, "wb") as f:
        f.write(b"FTYP" + b"\x00" * 65536)
    return path_str


# ===========================================================================
# 1. VCSI & Image Fallback Robustness Tests
# ===========================================================================
class TestVcsiAndImageFallbacks:
    """Tests simulated VCSI failures, timeouts, missing binaries, and Pillow placeholders."""

    def test_vcsi_nonzero_exit_code_falls_back_to_pillow(self, tmp_path):
        """Simulate VCSI failing with a non-zero exit code. Ensure Pillow fallback and warnings."""
        vid = make_synthetic_video(tmp_path / "scene_fail.mp4")
        cs_out = str(tmp_path / "preview.jpg")

        # Mock subprocess.run to simulate VCSI error
        mock_proc = subprocess.CompletedProcess(
            args=["vcsi"],
            returncode=1,
            stdout="",
            stderr="vcsi error: unsupported codec",
        )

        stderr_capture = io.StringIO()
        with patch("subprocess.run", return_value=mock_proc), patch("sys.stderr", stderr_capture):
            out_res = task.generate_contact_sheet(
                video_path=vid,
                out_path=cs_out,
                layout="4x4",
                timeout=10.0,
                pack_title="Failed VCSI Pack",
                scene_idx=0,
                total_scenes=1,
            )

        stderr_str = stderr_capture.getvalue()
        assert out_res == cs_out
        assert os.path.exists(cs_out)
        assert os.path.getsize(cs_out) > 0

        # Warning prefix \x01w\x02 must be emitted
        assert "\x01w\x02" in stderr_str
        assert "vcsi generation failed" in stderr_str or "Falling back to Pillow" in stderr_str

        # Verify image is a valid Pillow-generated JPEG
        with Image.open(cs_out) as img:
            assert img.format == "JPEG"
            assert img.size == (1280, 720)

    def test_vcsi_timeout_falls_back_to_pillow(self, tmp_path):
        """Simulate VCSI timing out after specified duration."""
        vid = make_synthetic_video(tmp_path / "scene_timeout.mp4")
        cs_out = str(tmp_path / "preview_timeout.jpg")

        stderr_capture = io.StringIO()
        with patch("shutil.which", return_value="vcsi"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["vcsi"], timeout=5.0)), \
             patch("sys.stderr", stderr_capture):
            out_res = task.generate_contact_sheet(
                video_path=vid,
                out_path=cs_out,
                layout="4x4",
                timeout=5.0,
                pack_title="Timeout Pack",
                scene_idx=0,
                total_scenes=1,
            )

        stderr_str = stderr_capture.getvalue()
        assert out_res == cs_out
        assert os.path.exists(cs_out)
        assert "\x01w\x02" in stderr_str
        assert "timed out after 5.0s" in stderr_str

        with Image.open(cs_out) as img:
            assert img.format == "JPEG"
            assert img.size == (1280, 720)

    def test_vcsi_missing_binary_falls_back_to_pillow(self, tmp_path):
        """Simulate missing VCSI binary / OSError on invocation."""
        vid = make_synthetic_video(tmp_path / "scene_nobin.mp4")
        cs_out = str(tmp_path / "preview_nobin.jpg")

        stderr_capture = io.StringIO()
        with patch("subprocess.run", side_effect=FileNotFoundError("vcsi not found")), \
             patch("sys.stderr", stderr_capture):
            out_res = task.generate_contact_sheet(
                video_path=vid,
                out_path=cs_out,
                layout="4x4",
                timeout=10.0,
                pack_title="NoBin Pack",
            )

        stderr_str = stderr_capture.getvalue()
        assert out_res == cs_out
        assert os.path.exists(cs_out)
        assert "\x01w\x02" in stderr_str
        assert "vcsi invocation error" in stderr_str or "Falling back to Pillow placeholder" in stderr_str

    def test_vcsi_zero_byte_corrupt_output_falls_back_to_pillow(self, tmp_path):
        """Simulate VCSI exiting 0 but producing a 0-byte file."""
        vid = make_synthetic_video(tmp_path / "scene_zerobyte.mp4")
        cs_out = str(tmp_path / "preview_zero.jpg")

        def mock_zero_byte(*args, **kwargs):
            # Create a 0-byte output file
            Path(cs_out).touch()
            return subprocess.CompletedProcess(args=["vcsi"], returncode=0)

        stderr_capture = io.StringIO()
        with patch("subprocess.run", side_effect=mock_zero_byte), patch("sys.stderr", stderr_capture):
            out_res = task.generate_contact_sheet(
                video_path=vid,
                out_path=cs_out,
                layout="4x4",
                timeout=10.0,
                pack_title="Zero Byte Output Pack",
            )

        assert os.path.exists(cs_out)
        # Should be replaced with valid Pillow placeholder (> 0 bytes)
        assert os.path.getsize(cs_out) > 1000
        with Image.open(cs_out) as img:
            assert img.format == "JPEG"

    def test_full_pack_build_with_mixed_vcsi_failures_succeeds(self, tmp_path, monkeypatch):
        """Full pack build with 3 scenes: Scene 1 succeeds, Scene 2 times out, Scene 3 errors.
        Must NOT halt pack generation and must generate valid torrent, manifest, and bbcode."""
        # Hermetic announce: payload announce is ignored unless allow_custom_announce
        # is opted in (Amendment B7), so the build falls back to the configured
        # announce. Pin it explicitly instead of leaking a dev-machine local
        # config value into the test outcome.
        monkeypatch.setattr(
            get_settings(),
            "empornium_announce_url",
            "http://tracker.empornium.sx:2710/passkey123/announce",
        )
        out_dir = str(tmp_path / "output_mixed")
        pack_title = "Mixed Fallback Pack"
        pack_dir = Path(out_dir) / sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        vid1 = make_synthetic_video(pack_dir / "scene1.mp4")
        vid2 = make_synthetic_video(pack_dir / "scene2.mp4")
        vid3 = make_synthetic_video(pack_dir / "scene3.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "include_contact_sheets": False,
            "scenes": [
                {"id": "1", "path": vid1, "performers": [{"name": "Performer Alpha"}]},
                {"id": "2", "path": vid2, "performers": [{"name": "Performer Beta"}]},
                {"id": "3", "path": vid3, "performers": [{"name": "Performer Gamma"}]},
            ],
            "announce": "http://tracker.empornium.sx:2710/passkey123/announce",
        }

        call_count = 0

        def side_effect_vcsi(video_path, out_path, layout="4x4", timeout=60.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Scene 1: Domain CS succeeds, creates dummy image
                img = Image.new("RGB", (640, 360), color=(100, 150, 200))
                img.save(out_path, format="JPEG")
                return True
            elif call_count == 2:
                # Scene 2: TimeoutExpired
                raise subprocess.TimeoutExpired(cmd=["vcsi"], timeout=timeout)
            else:
                # Scene 3: VCSI error
                return False

        with patch("task._domain_generate_contact_sheet", side_effect=side_effect_vcsi):
            result = task.run_build_megapack(payload)

        assert result["status"] == "success"
        assert os.path.exists(result["torrent_path"])
        assert os.path.exists(result["manifest_path"])
        assert os.path.exists(result["bbcode_path"])

        # 3 contact sheets generated
        assert len(result["contact_sheets"]) == 3
        for cs in result["contact_sheets"]:
            assert os.path.exists(cs)
            assert os.path.getsize(cs) > 0
            with Image.open(cs) as img:
                assert img.format == "JPEG"

        # Check torrent contents
        t = torf.Torrent.read(result["torrent_path"])
        assert t.name == sanitize_name(pack_title)
        assert len(t.files) == 3
        assert t.private is True

    def test_upload_previews_url_formatting(self):
        """Verify upload_previews formats file:/// URLs with forward slashes on Windows."""
        paths = [
            r"C:\Media\Output\pack_preview_1.jpg",
            r"D:\Media\Folder\pack_preview_2.jpg",
        ]
        urls = task.upload_previews(paths, {"upload_previews": False})
        assert urls == [
            "file:///C:/Media/Output/pack_preview_1.jpg",
            "file:///D:/Media/Folder/pack_preview_2.jpg",
        ]


# ===========================================================================
# 2. Payload Validation & Rejection (No Hollow Torrents / Manifests)
# ===========================================================================
class TestPayloadRejectionAndRobustness:
    """Tests early error rejection on empty, null, corrupt payloads without writing artifacts."""

    def test_empty_scenes_list_rejects_and_writes_no_artifacts(self, tmp_path):
        """Empty scenes list -> raises RuntimeError, 0 torrents/manifests/locks written."""
        out_dir = str(tmp_path / "out_empty")
        payload = {
            "pack_title": "Empty Pack",
            "output_dir": out_dir,
            "scenes": [],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
                task.run_build_megapack(payload)

        stderr_str = stderr_capture.getvalue()
        assert "\x01e\x02No valid media files found in scenes payload" in stderr_str

        # Ensure NO .torrent, .json, .txt, or .lock files exist in out_dir
        if os.path.exists(out_dir):
            created_files = os.listdir(out_dir)
            assert not any(f.endswith(".torrent") for f in created_files)
            assert not any(f.endswith("_manifest.json") for f in created_files)
            assert not any(f.endswith("_bbcode.txt") for f in created_files)
            assert not any(f.endswith(".lock") for f in created_files)

    def test_scenes_with_all_nonexistent_files_rejects(self, tmp_path):
        """Scenes referencing non-existent files -> raises RuntimeError, writes no artifacts."""
        out_dir = str(tmp_path / "out_missing")
        payload = {
            "pack_title": "Missing Media Pack",
            "output_dir": out_dir,
            "scenes": [
                {"id": "1", "path": "C:\\does_not_exist_abc123.mp4"},
                {"id": "2", "file_paths": ["D:\\ghost_file_xyz789.mkv"]},
            ],
        }

        with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
            task.run_build_megapack(payload)

        if os.path.exists(out_dir):
            created_files = os.listdir(out_dir)
            assert not any(f.endswith(".torrent") for f in created_files)
            assert not any(f.endswith(".lock") for f in created_files)

    def test_scenes_with_bogus_null_elements_rejects_safely(self, tmp_path):
        """Scenes list containing None, {}, non-dict objects -> handled safely without unhandled exception."""
        out_dir = str(tmp_path / "out_bogus")
        payload = {
            "pack_title": "Bogus Elements Pack",
            "output_dir": out_dir,
            "scenes": [None, {}, {"id": "10"}, {"files": []}, 12345, "invalid"],
        }

        with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
            task.run_build_megapack(payload)

        if os.path.exists(out_dir):
            assert not any(f.endswith(".torrent") for f in os.listdir(out_dir))

    def test_stdin_parsing_empty_string(self):
        """Stdin is completely empty string -> returns mode='build', empty payload dict."""
        with patch("sys.stdin", io.StringIO("")), patch("sys.argv", ["task.py"]):
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload == {}
            assert conn == {}

    def test_stdin_parsing_null_json(self):
        """Stdin is 'null' JSON -> returns mode='build', empty payload dict."""
        with patch("sys.stdin", io.StringIO("null\n")), patch("sys.argv", ["task.py"]):
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload == {}
            assert conn == {}

    def test_stdin_parsing_corrupt_json(self):
        """Stdin is corrupt JSON syntax -> handles gracefully, logs warning, returns empty dict."""
        corrupt_inputs = [
            "{ scenes: [ missing quotes }",
            "<!DOCTYPE html><html><body>Error</body></html>",
            "[1, 2, 3, broken",
            "   \t\n  ",
            "true",
            "12345",
            '"string"',
        ]

        for corrupt_in in corrupt_inputs:
            stderr_capture = io.StringIO()
            with patch("sys.stdin", io.StringIO(corrupt_in)), \
                 patch("sys.argv", ["task.py"]), \
                 patch("sys.stderr", stderr_capture):
                mode, payload, conn = task.parse_input_payload()
                assert isinstance(payload, dict)
                assert isinstance(conn, dict)

    def test_stdin_plugin_arg_input_nested_json(self):
        """Stdin uses Stash's native PluginArgInput format with nested JSON payload."""
        sample_stash_input = json.dumps({
            "task_name": "BuildMegapack",
            "server_connection": {"port": 9999, "ApiKey": "test-key"},
            "args": [
                {"key": "mode", "value": {"str": "build"}},
                {
                    "key": "payload",
                    "value": json.dumps({
                        "pack_title": "PluginArgInput Pack",
                        "scenes": [],
                    })
                }
            ]
        })

        with patch("sys.stdin", io.StringIO(sample_stash_input)), patch("sys.argv", ["task.py"]):
            mode, payload, conn = task.parse_input_payload()
            assert mode == "build"
            assert payload.get("pack_title") == "PluginArgInput Pack"
            assert payload.get("scenes") == []
            assert conn.get("ApiKey") == "test-key"

    def test_main_entrypoint_with_empty_payload_exits_code_1_and_no_hollow_files(self, tmp_path):
        """Executing main() with empty payload exits with code 1 and writes no files."""
        out_dir = str(tmp_path / "main_empty_test")
        empty_payload = json.dumps({
            "pack_title": "Main Empty Pack",
            "output_dir": out_dir,
            "scenes": [],
        })

        stderr_capture = io.StringIO()
        with patch("sys.stdin", io.StringIO(empty_payload)), \
             patch("sys.argv", ["task.py"]), \
             patch("sys.stderr", stderr_capture), \
             pytest.raises(SystemExit) as exc_info:
            task.main()

        assert exc_info.value.code == 1
        stderr_str = stderr_capture.getvalue()
        assert "\x01e\x02" in stderr_str
        assert "Task execution failed" in stderr_str or "No valid media files found" in stderr_str

        # Confirm no artifacts created
        if os.path.exists(out_dir):
            assert not any(f.endswith(".torrent") for f in os.listdir(out_dir))
            assert not any(f.endswith(".lock") for f in os.listdir(out_dir))


# ===========================================================================
# 3. Lockfile Lifecycle & Failure Safety
# ===========================================================================
class TestLockfileLifecycle:
    """Tests lockfile locking, stale reclamation, and cleanup on unhandled failure."""

    def test_stale_lockfile_with_dead_pid_is_reclaimed(self, tmp_path):
        """A lockfile referencing a dead PID must be automatically reclaimed."""
        out_dir = str(tmp_path / "out_lock")
        os.makedirs(out_dir, exist_ok=True)
        pack_title = "Stale Lock Pack"
        safe_title = sanitize_name(pack_title)
        pack_dir = Path(out_dir) / safe_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        lock_path = os.path.join(out_dir, f".{safe_title}.lock")

        # Write lockfile with guaranteed non-existent PID (e.g. 9999999)
        with open(lock_path, "w") as f:
            f.write(f"pid=9999999\nstarted={time.time() - 500}\npack=Stale Lock Pack\n")

        vid = make_synthetic_video(pack_dir / "scene_lock.mp4")
        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [{"id": "1", "path": vid}],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            result = task.run_build_megapack(payload)

        assert result["status"] == "success"
        assert "\x01w\x02Reclaiming stale lockfile" in stderr_capture.getvalue()
        # Lockfile must be removed upon completion
        assert not os.path.exists(lock_path)

    def test_active_lockfile_with_living_pid_blocks_concurrent_build(self, tmp_path):
        """A lockfile referencing the current (living) PID must raise RuntimeError."""
        out_dir = str(tmp_path / "out_active_lock")
        os.makedirs(out_dir, exist_ok=True)
        pack_title = "Active Lock Pack"
        safe_title = sanitize_name(pack_title)
        pack_dir = Path(out_dir) / safe_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        lock_path = os.path.join(out_dir, f".{safe_title}.lock")

        # Write lockfile with current PID
        with open(lock_path, "w") as f:
            f.write(f"pid={os.getpid()}\nstarted={time.time()}\npack=Active Lock Pack\n")

        vid = make_synthetic_video(pack_dir / "scene_active.mp4")
        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [{"id": "1", "path": vid}],
        }

        with pytest.raises(RuntimeError, match="Concurrent build in progress"):
            task.run_build_megapack(payload)

    def test_lockfile_cleaned_up_if_exception_occurs_during_build(self, tmp_path):
        """If an exception occurs midway through build, lockfile is deleted in finally block."""
        out_dir = str(tmp_path / "out_crash")
        os.makedirs(out_dir, exist_ok=True)
        pack_title = "Crash Pack"
        safe_title = sanitize_name(pack_title)
        pack_dir = Path(out_dir) / safe_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        lock_path = os.path.join(out_dir, f".{safe_title}.lock")

        vid = make_synthetic_video(pack_dir / "scene_crash.mp4")
        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [{"id": "1", "path": vid}],
        }

        with patch("task.create_torrent", side_effect=TorrentError("Simulated Disk Failure")):
            with pytest.raises(TorrentError):
                task.run_build_megapack(payload)

        # Lockfile MUST NOT linger
        assert not os.path.exists(lock_path)


# ===========================================================================
# 4. Torrent Progress Streaming Callback Seam Verification
# ===========================================================================
class TestTorrentProgressCallbackSeam:
    """Verifies create_torrent accepts callback and task.py streams progress via \\x01p\\x02."""

    def test_create_torrent_invokes_callback(self, tmp_path):
        """Verify create_torrent passes progress callback to torf."""
        media_file = make_synthetic_video(tmp_path / "test_callback.mp4")
        out_torrent = tmp_path / "test.torrent"

        callback_invocations = []

        def dummy_cb(torrent_obj, filepath, pieces_done, total_pieces):
            callback_invocations.append((pieces_done, total_pieces))

        res = create_torrent(
            payload_dir=tmp_path,
            out_path=out_torrent,
            name="Callback Test",
            callback=dummy_cb,
        )

        assert res["infohash"]
        assert len(callback_invocations) > 0
        last_done, last_total = callback_invocations[-1]
        assert last_done == last_total

    def test_build_megapack_streams_hashing_progress(self, tmp_path):
        """Verify run_build_megapack streams 70%->90% progress during torrent hashing."""
        out_dir = str(tmp_path / "out_stream")
        os.makedirs(out_dir, exist_ok=True)
        pack_title = "Hashing Stream Pack"
        safe_title = sanitize_name(pack_title)
        pack_dir = Path(out_dir) / safe_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        vid = make_synthetic_video(pack_dir / "hash_stream.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": out_dir,
            "scenes": [{"id": "1", "path": vid}],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            result = task.run_build_megapack(payload)

        assert result["status"] == "success"
        stderr_str = stderr_capture.getvalue()

        # Parse \x01p\x02 lines
        p_lines = [l for l in stderr_str.splitlines() if l.startswith("\x01p\x02")]
        assert len(p_lines) >= 4

        # Hashing progress occurs in [0.70, 0.90]
        hashing_values = [
            float(l.replace("\x01p\x02", ""))
            for l in p_lines
            if 0.70 <= float(l.replace("\x01p\x02", "")) <= 0.90
        ]
        assert len(hashing_values) > 0
        assert "\x01i\x02[70%] Generating .torrent file..." in stderr_str
        assert any("Hashing torrent pieces" in l for l in stderr_str.splitlines())
