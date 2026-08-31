"""
Test suite for Requirement R5: Preview Engine & Content-Asserting Tests.
Validates genuine contact sheet generation, torf torrent creation, layout options,
safe preview uploads, logging protocol, piece hashing progress, and announce trackers.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageStat
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


def _dummy_media_path():
    global _DUMMY_MEDIA_DIR
    if _DUMMY_MEDIA_DIR is None:
        _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
    p = os.path.join(_DUMMY_MEDIA_DIR, "dummy_media.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def ensure_task_tools_on_path():
    """Ensure the Stash tools dir and the active interpreter's Scripts dir (vcsi) are on PATH during tests."""
    stash_dir = Path.home() / ".stash"
    venv_scripts = Path(sys.executable).parent
    old_path = os.environ.get("PATH", "")
    new_parts = []
    if stash_dir.exists() and str(stash_dir) not in old_path:
        new_parts.append(str(stash_dir))
    if venv_scripts.exists() and str(venv_scripts) not in old_path:
        new_parts.append(str(venv_scripts))
    if new_parts:
        os.environ["PATH"] = ";".join(new_parts) + ";" + old_path
    yield
    os.environ["PATH"] = old_path


@pytest.fixture
def create_test_video(tmp_path):
    """Factory fixture to create synthetic test videos using ffmpeg lavfi testsrc."""
    def _create(name="scene.mp4", pattern="testsrc", duration=2, size="320x240", rate=10, target_dir=None):
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg executable not found in PATH")
        video_path = (Path(target_dir) if target_dir else tmp_path) / name
        video_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"{pattern}=duration={duration}:size={size}:rate={rate}",
            "-pix_fmt", "yuv420p",
            "-y",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            pytest.skip(f"ffmpeg failed to create test video: {res.stderr}")
        return video_path
    return _create


# ============================================================================
# 1. GENUINE CONTACT SHEET GENERATION (R1 / R5)
# ============================================================================

class TestGenuineContactSheets:
    """Tests verifying real contact sheet generation with vcsi and pixel variance assertions."""

    def test_real_contact_sheet_high_color_variance(self, tmp_path, create_test_video):
        """Contact sheet generated for actual video must exhibit high pixel color variance."""
        if not shutil.which("vcsi"):
            pytest.skip("vcsi executable not found in PATH")

        out_dir = tmp_path / "contact_out"
        pack_title = "VarianceTestPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("scene1.mp4", pattern="testsrc", duration=2, target_dir=pack_dir)
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "layout": "grid_3x3",
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        assert len(result["contact_sheets"]) == 1
        cs_path = result["contact_sheets"][0]
        assert os.path.exists(cs_path)

        # Open with Pillow and measure variance across RGB channels
        with Image.open(cs_path) as img:
            stat = ImageStat.Stat(img)
            # High variance across RGB channels indicates real video frames, not placeholder
            assert all(v > 500 for v in stat.var), f"Variance too low: {stat.var}"
            assert all(s > 20 for s in stat.stddev), f"Stddev too low: {stat.stddev}"

    def test_multi_scene_contact_sheet_naming_and_variance(self, tmp_path, create_test_video):
        """Megapack with multiple scenes produces indexed contact sheets with real frame variance."""
        if not shutil.which("vcsi"):
            pytest.skip("vcsi executable not found in PATH")

        out_dir = tmp_path / "multi_cs_out"
        pack_title = "MultiScenePack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("s1.mp4", pattern="testsrc", duration=2, target_dir=pack_dir)
        v2 = create_test_video("s2.mp4", pattern="smptebars", duration=2, target_dir=pack_dir)
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "layout": "grid_2x2",
            "scenes": [
                {"id": 101, "path": str(v1)},
                {"id": 102, "path": str(v2)},
            ],
        }

        result = task.run_build_megapack(payload)
        assert result["status"] == "success"
        assert len(result["contact_sheets"]) == 2

        cs1, cs2 = result["contact_sheets"]
        assert cs1.endswith("MultiScenePack_preview_1.jpg")
        assert cs2.endswith("MultiScenePack_preview_2.jpg")
        assert os.path.exists(cs1) and os.path.exists(cs2)

        for cs in (cs1, cs2):
            with Image.open(cs) as img:
                stat = ImageStat.Stat(img)
                assert all(v > 500 for v in stat.var), f"Low variance for {cs}: {stat.var}"

    def test_contact_sheet_fallback_on_corrupt_video(self, tmp_path):
        """Corrupt or non-video media emits warning \\x01w\\x02 and falls back to Pillow placeholder."""
        bad_video = tmp_path / "corrupt_video.mp4"
        bad_video.write_bytes(b"NOT_A_REAL_VIDEO_HEADER_1234567890")
        out_img = tmp_path / "fallback.jpg"

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            res_path = task.generate_contact_sheet(
                video_path=str(bad_video),
                out_path=str(out_img),
                layout="4x4",
                timeout=10.0,
                pack_title="CorruptPack",
            )

        assert os.path.exists(res_path)
        err_output = stderr_capture.getvalue()
        assert "\x01w\x02" in err_output
        assert "Falling back to Pillow placeholder" in err_output

        # Verify fallback image is a valid Pillow-generated JPEG
        with Image.open(res_path) as img:
            assert img.format == "JPEG"
            assert img.size == (1280, 720)
            stat = ImageStat.Stat(img)
            # Placeholder has solid background with text, so variance is very low (< 100)
            assert all(v < 100 for v in stat.var)

    def test_contact_sheet_fallback_on_vcsi_timeout(self, tmp_path, create_test_video):
        """Timeout in vcsi subprocess falls back cleanly with \\x01w\\x02 warning."""
        v1 = create_test_video("timeout_test.mp4", duration=2)
        out_img = tmp_path / "timeout_cs.jpg"

        # Force timeout by setting timeout to 0.0001 seconds
        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            res_path = task.generate_contact_sheet(
                video_path=str(v1),
                out_path=str(out_img),
                layout="4x4",
                timeout=0.0001,
                pack_title="TimeoutPack",
            )

        assert os.path.exists(res_path)
        err_output = stderr_capture.getvalue()
        assert "\x01w\x02" in err_output
        assert "timed out" in err_output


# ============================================================================
# 2. GENUINE TORRENT CREATION WITH TORF (R2 / R5)
# ============================================================================

class TestGenuineTorrentCreation:
    """Tests verifying genuine torrent construction and piece hashing via torf."""

    def test_real_torrent_creation_and_hashes(self, tmp_path, create_test_video):
        """Generated .torrent parses with torf.Torrent.read() with valid piece hashes and file list."""
        out_dir = tmp_path / "torrent_test_out"
        pack_title = "TorfIntegrityPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("video1.mp4", duration=2, target_dir=pack_dir)
        v2 = create_test_video("video2.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": [
                {"id": 1, "path": str(v1)},
                {"id": 2, "path": str(v2)},
            ],
        }

        result = task.run_build_megapack(payload)
        torrent_path = result["torrent_path"]
        assert os.path.exists(torrent_path)

        # Read back torrent with torf
        t = torf.Torrent.read(torrent_path)
        assert t.name == pack_title
        assert t.pieces > 0
        assert len(t.hashes) > 0
        assert len(t.hashes) == t.pieces

        # Verify files listed inside torrent
        file_names = [os.path.basename(f) for f in t.files]
        assert "video1.mp4" in file_names
        assert "video2.mp4" in file_names

        # Total torrent size must match sum of video file sizes (no contact sheets)
        expected_size = os.path.getsize(str(v1)) + os.path.getsize(str(v2))
        assert t.size == expected_size

    def test_torrent_piece_size_calculation(self):
        """Piece size exponents are clamped between 16 KiB (2^14) and 8 MiB (2^23)."""
        assert task.calculate_piece_size(0) == 16384
        assert task.calculate_piece_size(-500) == 16384
        assert task.calculate_piece_size(1024) == 16384
        assert task.calculate_piece_size(100 * 1024 * 1024) == 65536
        assert task.calculate_piece_size(10 * 1024 * 1024 * 1024) == 8388608
        # Boundary checks
        assert task.calculate_piece_size(1) >= 16384
        assert task.calculate_piece_size(100 * 1024 * 1024 * 1024) <= 8388608

    def test_torrent_hashing_progress_streaming(self, tmp_path, create_test_video):
        """Piece hashing streams monotonic \\x01p\\x02 progress between 0.70 and 0.90."""
        out_dir = tmp_path / "hash_prog_out"
        pack_title = "HashProgressPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("hash_v1.mp4", duration=2, target_dir=pack_dir)

        stderr_capture = io.StringIO()
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        with patch("sys.stderr", stderr_capture):
            task.run_build_megapack(payload)

        stderr_text = stderr_capture.getvalue()
        progress_lines = [
            line for line in stderr_text.splitlines() if line.startswith("\x01p\x02")
        ]
        assert len(progress_lines) > 0

        # Extract floats
        progress_values = [float(p.replace("\x01p\x02", "")) for p in progress_lines]

        # Check hashing progress values (between 0.70 and 0.90)
        hashing_progress = [pv for pv in progress_values if 0.70 <= pv <= 0.90]
        assert len(hashing_progress) >= 2, f"Expected hashing progress updates: {hashing_progress}"

        # Must be non-decreasing
        for i in range(1, len(hashing_progress)):
            assert hashing_progress[i] >= hashing_progress[i - 1], f"Progress not monotonic: {hashing_progress}"


# ============================================================================
# 3. ANNOUNCE TRACKERS & CONTACT SHEET TOGGLES (R2 / R5)
# ============================================================================

class TestTrackersAndContactSheetToggle:
    """Tests verifying announce tracker configurations and contact sheet torrent inclusion."""

    def test_custom_announce_single_tracker(self, tmp_path, create_test_video):
        """Single announce tracker string sets torrent tracker and private flag."""
        out_dir = tmp_path / "single_tracker_out"
        pack_title = "SingleTrackerPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("tracker_v1.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "announce": "http://tracker.example.com:8080/announce",
            "allow_custom_announce": True,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        assert t.trackers == [["http://tracker.example.com:8080/announce"]]
        assert t.private is True

    def test_custom_announce_multiple_trackers(self, tmp_path, create_test_video):
        """Multiple announce tracker URLs list sets torrent trackers."""
        out_dir = tmp_path / "multi_tracker_out"
        pack_title = "MultiTrackerPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("trackers_v2.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "allow_custom_announce": True,
            "announce": [
                "http://tracker1.example.com/announce",
                "http://tracker2.example.com/announce",
            ],
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        flat_trackers = [tr for tier in t.trackers for tr in tier]
        assert "http://tracker1.example.com/announce" in flat_trackers
        assert "http://tracker2.example.com/announce" in flat_trackers

    def test_trackerless_torrent_when_omitted(self, tmp_path, create_test_video):
        """When announce is omitted and custom announce enabled with empty trackers, torrent is trackerless."""
        out_dir = tmp_path / "trackerless_out"
        pack_title = "TrackerlessPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("trackerless.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "allow_custom_announce": True,
            "announce": None,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        assert t.trackers == [] or t.trackers is None

    def test_include_contact_sheets_toggle_true(self, tmp_path, create_test_video):
        """When include_contact_sheets is True, contact sheets are included in Contact Sheets/ inside torrent."""
        out_dir = tmp_path / "cs_torrent_out"
        pack_title = "WithContactSheetsPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_with_cs.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": True,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        # Must contain video file and contact sheet file under Contact Sheets
        has_video = any("v_with_cs.mp4" in fp for fp in file_paths)
        has_contact_sheet = any("Contact Sheets" in fp for fp in file_paths)
        assert has_video, f"Video missing in torrent files: {file_paths}"
        assert has_contact_sheet, f"Contact sheet missing in torrent files: {file_paths}"

    def test_contact_sheets_included_by_default(self, tmp_path, create_test_video):
        """Megapack default: key omitted → contact sheets ARE included in torrent (site rule)."""
        out_dir = tmp_path / "no_cs_torrent_out"
        pack_title = "WithoutContactSheetsPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_no_cs.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp for fp in file_paths)
        assert has_contact_sheet, f"Contact sheet missing in default torrent: {file_paths}"

    def test_include_contact_sheets_explicit_false_opt_out(self, tmp_path, create_test_video):
        """Explicit False still opts out — torrent contains media files only."""
        out_dir = tmp_path / "explicit_false_cs_out"
        pack_title = "ExplicitFalseCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_explicit_false.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp or fp.endswith(".jpg") for fp in file_paths)
        assert not has_contact_sheet, f"Contact sheet found despite explicit False: {file_paths}"

    def test_contact_sheets_included_by_default_multi_scene(self, tmp_path, create_test_video):
        """Megapack default with 2 scenes: key omitted → exactly 2 jpg entries under Contact Sheets/."""
        out_dir = tmp_path / "multi_cs_default_out"
        pack_title = "MultiSceneDefaultCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("scene_a.mp4", duration=2, target_dir=pack_dir)
        v2 = create_test_video("scene_b.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [
                {"id": 1, "path": str(v1)},
                {"id": 2, "path": str(v2)},
            ],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        cs_entries = [fp for fp in file_paths if "Contact Sheets" in fp and fp.endswith(".jpg")]
        assert len(cs_entries) == 2, f"Expected 2 Contact Sheets jpg entries, got {len(cs_entries)}: {cs_entries}"

    def test_string_false_opts_out(self, tmp_path, create_test_video):
        """String "false" (task-args path) opts out — torrent is media-only."""
        out_dir = tmp_path / "string_false_cs_out"
        pack_title = "StringFalseCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_string_false.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": "false",
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp or fp.endswith(".jpg") for fp in file_paths)
        assert not has_contact_sheet, f"Contact sheet found despite string 'false': {file_paths}"

    def test_string_true_includes_sheets(self, tmp_path, create_test_video):
        """String "true" (task-args path) opts in — Contact Sheets present in torrent."""
        out_dir = tmp_path / "string_true_cs_out"
        pack_title = "StringTrueCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_string_true.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": "true",
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp for fp in file_paths)
        assert has_contact_sheet, f"Contact sheet missing despite string 'true': {file_paths}"

    def test_singular_alias_true_includes_sheets(self, tmp_path, create_test_video):
        """Singular alias "include_contact_sheet": True (no plural key) → Contact Sheets present."""
        out_dir = tmp_path / "alias_true_cs_out"
        pack_title = "AliasTrueCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_alias_true.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheet": True,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp for fp in file_paths)
        assert has_contact_sheet, f"Contact sheet missing despite singular alias True: {file_paths}"

    def test_singular_alias_false_opts_out(self, tmp_path, create_test_video):
        """Singular alias "include_contact_sheet": False (no plural key) → media-only."""
        out_dir = tmp_path / "alias_false_cs_out"
        pack_title = "AliasFalseCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_alias_false.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheet": False,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp or fp.endswith(".jpg") for fp in file_paths)
        assert not has_contact_sheet, f"Contact sheet found despite singular alias False: {file_paths}"

    def test_plural_key_wins_over_alias(self, tmp_path, create_test_video):
        """Both keys present: plural False + singular True → media-only (plural first-present wins)."""
        out_dir = tmp_path / "plural_wins_cs_out"
        pack_title = "PluralWinsCSPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("v_plural_wins.mp4", duration=2, target_dir=pack_dir)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "include_contact_sheet": True,
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        result = task.run_build_megapack(payload)
        t = torf.Torrent.read(result["torrent_path"])
        file_paths = [str(f) for f in t.files]
        has_contact_sheet = any("Contact Sheets" in fp or fp.endswith(".jpg") for fp in file_paths)
        assert not has_contact_sheet, f"Contact sheet found despite plural key winning with False: {file_paths}"


# ============================================================================
# 4. LAYOUT PARAMETER PARSING & NORMALIZATION (R1 / R5)
# ============================================================================

class TestLayoutParameterHonored:
    """Tests verifying grid layout parsing, normalization, and vcsi CLI argument integration."""

    @pytest.mark.parametrize(
        "input_layout, expected_normalized",
        [
            ("grid_4x4", "4x4"),
            ("4x4", "4x4"),
            ("grid_3x3", "3x3"),
            ("3x3", "3x3"),
            ("grid_2x5", "2x5"),
            ("2x5", "2x5"),
            ("GRID_5X5", "5x5"),
            ("grid_1x10", "1x10"),
            (None, "4x4"),
            ("", "4x4"),
            ("invalid_layout", "4x4"),
        ],
    )
    def test_normalize_grid_layout_formats(self, input_layout, expected_normalized):
        """normalize_grid_layout correctly maps various user formats to NxM."""
        assert task.normalize_grid_layout(input_layout) == expected_normalized

    def test_layout_alters_vcsi_command_arguments(self, tmp_path):
        """Non-default layout alters the '-g' parameter passed to vcsi."""
        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"dummy")
        out_cs = tmp_path / "out.jpg"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Simulate output file creation
            out_cs.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            task.generate_contact_sheet(
                video_path=str(dummy_video),
                out_path=str(out_cs),
                layout="grid_2x5",
            )

            assert mock_run.called
            cmd_args = mock_run.call_args[0][0]
            assert "-g" in cmd_args
            g_idx = cmd_args.index("-g")
            assert cmd_args[g_idx + 1] == "2x5"


# ============================================================================
# 5. EMPTY AND INVALID INPUT ERROR HANDLING (R2 / R5)
# ============================================================================

class TestEmptyAndInvalidInputs:
    """Tests asserting loud error signaling when input media is missing or invalid."""

    def test_empty_scenes_raises_and_writes_no_artifacts(self, tmp_path):
        """Empty scenes payload must RAISE, not return success.

        Regression guard. This previously asserted status == "success", which
        codified the bug: the build emitted a torrent with zero piece hashes --
        rejected by torf as "Invalid metainfo: ['info']['pieces'] is empty" --
        under a job Stash reported as FINISHED.
        """
        out_dir = tmp_path / "empty_scenes_out"
        payload = {
            "pack_title": "EmptyScenesPack",
            "output_dir": str(out_dir),
            # Intentionally empty -- this is the case under test. Do not "fix" this
            # by supplying media; that is what defeated the original version.
            "scenes": [],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
                task.run_build_megapack(payload)

        err_output = stderr_capture.getvalue()
        assert "\x01e\x02" in err_output
        assert "No valid media files found" in err_output

        # No unusable artifacts may be left behind.
        safe_title = task.sanitize_name("EmptyScenesPack")
        assert not (out_dir / f"{safe_title}.torrent").exists()
        assert not (out_dir / f"{safe_title}_manifest.json").exists()

        # Lockfile must still be released on the failure path.
        assert not (out_dir / f".{safe_title}.lock").exists()

    def test_nonexistent_scene_paths_raise(self, tmp_path):
        """Scenes whose paths do not exist must RAISE, not emit a hollow pack."""
        out_dir = tmp_path / "missing_files_out"
        payload = {
            "pack_title": "MissingFilesPack",
            "output_dir": str(out_dir),
            "scenes": [
                {"id": 1, "path": str(tmp_path / "does_not_exist_1.mp4")},
                {"id": 2, "path": str(tmp_path / "does_not_exist_2.mp4")},
            ],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            with pytest.raises(RuntimeError, match="refusing to emit an empty pack"):
                task.run_build_megapack(payload)

        err_output = stderr_capture.getvalue()
        assert "\x01e\x02" in err_output
        assert "No valid media files found" in err_output

        safe_title = task.sanitize_name("MissingFilesPack")
        assert not (out_dir / f"{safe_title}.torrent").exists()
        assert not (out_dir / f".{safe_title}.lock").exists()


# ============================================================================
# 6. SAFE PREVIEW UPLOAD EXTENSION POINT (R3 / R5)
# ============================================================================

class TestUploadPreviewsSafety:
    """Tests verifying upload_previews is disabled by default and generates local file:/// URLs."""

    def test_upload_disabled_by_default_zero_network_calls(self):
        """By default, upload_previews makes 0 network calls and returns file:/// URLs."""
        test_paths = [
            r"C:\Media\previews\preview1.jpg",
            r"C:\Media\previews\preview2.jpg",
        ]

        # Block any network socket creation to guarantee 0 outbound calls
        with patch.object(socket, "socket", side_effect=RuntimeError("Outbound network attempted!")):
            urls = task.upload_previews(test_paths)

        assert len(urls) == 2
        assert urls[0] == "file:///C:/Media/previews/preview1.jpg"
        assert urls[1] == "file:///C:/Media/previews/preview2.jpg"

    def test_upload_previews_windows_path_normalization(self):
        """Windows backslashes are normalized to forward slashes in file:/// URLs."""
        paths = [r"D:\Media\Packs\My Pack\preview_1.jpg"]
        urls = task.upload_previews(paths, config={"upload_previews": False})
        assert urls[0] == "file:///D:/Media/Packs/My Pack/preview_1.jpg"

    def test_upload_previews_empty_list(self):
        """Empty input list returns empty output list."""
        assert task.upload_previews([]) == []
        assert task.upload_previews(None) == []


# ============================================================================
# 7. INFORMATIONAL LOGGING PROTOCOL CONFORMANCE (R4 / R5)
# ============================================================================

class TestLoggingProtocolConformance:
    """Tests verifying Stash logging control character prefixes."""

    def test_emit_progress_info_prefix(self):
        """emit_progress prefixes human status message with \\x01i\\x02."""
        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            task.emit_progress(0.45, "Generating contact sheets")

        lines = stderr_capture.getvalue().splitlines()
        assert lines[0] == "\x01p\x020.4500"
        assert lines[1] == "\x01i\x02[45%] Generating contact sheets"

    def test_emit_progress_without_message(self):
        """emit_progress without message emits only the exact \\x01p\\x02 progress line."""
        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            task.emit_progress(0.85)

        lines = stderr_capture.getvalue().splitlines()
        assert len(lines) == 1
        assert lines[0] == "\x01p\x020.8500"

    def test_full_megapack_build_protocol_lines(self, tmp_path, create_test_video):
        """All stderr lines during run_build_megapack conform to \\x01p\\x02, \\x01i\\x02, \\x01w\\x02, or \\x01e\\x02."""
        out_dir = tmp_path / "proto_out"
        pack_title = "ProtocolConformancePack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("proto_v1.mp4", duration=2, target_dir=pack_dir)
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(v1)}],
        }

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            task.run_build_megapack(payload)

        stderr_lines = [l for l in stderr_capture.getvalue().splitlines() if l.strip()]
        for line in stderr_lines:
            assert line.startswith(("\x01p\x02", "\x01i\x02", "\x01w\x02", "\x01e\x02")), (
                f"Line does not conform to Stash protocol: {repr(line)}"
            )


# ============================================================================
# 8. MANIFEST & SUMMARY SCHEMA INTEGRITY (CONSTRAINTS / R5)
# ============================================================================

class TestManifestAndSummarySchema:
    """Tests verifying manifest JSON and summary schema keys remain intact."""

    def test_manifest_schema_and_bbcode_content(self, tmp_path, create_test_video):
        """Manifest schema and BBCode output preserve all required fields."""
        out_dir = tmp_path / "schema_out"
        pack_title = "SchemaPack"
        pack_dir = out_dir / pack_title
        pack_dir.mkdir(parents=True, exist_ok=True)
        v1 = create_test_video("schema_v1.mp4", duration=2, target_dir=pack_dir)
        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "performers": ["Star A", "Star B"],
            "tags": ["4K", "Feature"],
            "notes": "Important pack notes.",
            "scenes": [{"id": 1, "path": str(v1), "title": "Scene 1"}],
        }

        result = task.run_build_megapack(payload)
        # Check return dict schema
        for required_key in [
            "status",
            "pack_title",
            "torrent_path",
            "manifest_path",
            "bbcode_path",
            "contact_sheets",
            "uploaded_urls",
            "bbcode",
        ]:
            assert required_key in result

        # Check manifest JSON file on disk
        manifest_file = Path(result["manifest_path"])
        assert manifest_file.exists()
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_json = json.load(f)

        assert manifest_json["pack_title"] == "SchemaPack"
        assert manifest_json["scene_count"] == 1
        assert "torrent_path" in manifest_json
        assert "bbcode_path" in manifest_json
        assert "contact_sheets" in manifest_json
        assert "uploaded_urls" in manifest_json

        # Check BBCode content
        bbcode = result["bbcode"]
        assert "[b]Performers:[/b] Star A & Star B" in bbcode
        assert "[b]Tags:[/b] 4K, Feature" in bbcode
        assert "[quote]Important pack notes.[/quote]" in bbcode
        assert "[img=200]file:///" in bbcode
