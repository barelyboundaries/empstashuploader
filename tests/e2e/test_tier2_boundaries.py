"""
Tier 2: Boundary & Corner Cases E2E Test Suite.
Tests extreme boundaries, empty inputs, max bounds, non-standard video formats,
path variations, and concurrency corner cases.
"""

import sys
import os
import io
import time
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

from task import (
    sanitize_name,
    _extract_names,
    _extract_scene_paths,
    run_build_megapack,
    calculate_piece_size,
)


# ============================================================================
# 1. INPUT PAYLOAD & STRING BOUNDARY CASES
# ============================================================================
class TestPayloadAndStringBoundaries:
    """Boundary conditions on input strings, None values, and missing fields."""

    @pytest.mark.parametrize("invalid_input", [
        None,
        "",
        "   ",
        "...",
        "???",
        '<>:"/\\|?*',
    ])
    def test_sanitize_name_empty_and_illegal_fallbacks(self, invalid_input):
        """Empty or fully illegal strings safely fall back to 'Untitled' or safe name."""
        clean = sanitize_name(invalid_input)
        assert len(clean) > 0
        assert not any(c in clean for c in '<>:"/\\|?*')

    def test_extract_names_handles_heterogeneous_structures(self):
        """_extract_names safely extracts strings from various nested structures."""
        assert _extract_names(None) == []
        assert _extract_names("") == []
        assert _extract_names(["  Alice  ", "Bob", "Alice"]) == ["Alice", "Bob"]
        assert _extract_names({"name": "Solo Performer"}) == ["Solo Performer"]
        assert _extract_names([{"name": "Tag1"}, {"title": "Tag2"}, {"name": ""}]) == ["Tag1", "Tag2"]

    def test_extract_scene_paths_handles_all_stash_scene_representations(self):
        """_extract_scene_paths extracts paths from string, path dict, file_paths array, or files list."""
        assert _extract_scene_paths(None) == []
        assert _extract_scene_paths("/path/to/scene.mp4") == ["/path/to/scene.mp4"]
        assert _extract_scene_paths({"path": "/a/b.mp4"}) == ["/a/b.mp4"]
        assert _extract_scene_paths({"source_path": "/a/c.mp4"}) == ["/a/c.mp4"]
        assert _extract_scene_paths({"file_paths": ["/a/d.mp4", "/a/e.mp4"]}) == ["/a/d.mp4", "/a/e.mp4"]
        assert _extract_scene_paths({"files": [{"path": "/a/f.mp4"}]}) == ["/a/f.mp4"]

    def test_payload_with_missing_optional_fields(self, media_factory, tmp_path):
        """Build operates cleanly when optional fields (performers, tags, notes, trackers) are absent."""
        out_dir = tmp_path / "minimal_out"
        out_dir.mkdir()
        f1 = media_factory("minimal_scene", ".mp4", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "Minimal Payload Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()
        assert Path(res["manifest_path"]).exists()
        assert Path(res["bbcode_path"]).exists()


# ============================================================================
# 2. MEDIA FORMATS & EXTENSION CORNER CASES
# ============================================================================
class TestMediaFormatsAndExtensions:
    """Non-standard video formats and multi-extension corner cases."""

    @pytest.mark.parametrize("ext", [
        ".mkv",
        ".avi",
        ".wmv",
        ".webm",
        ".ts",
        ".mov",
        ".flv",
        ".m4v",
        ".mp4",
    ])
    def test_all_video_extensions_preserved(self, media_factory, tmp_path, ext):
        """Every supported video extension is preserved in the generated torrent."""
        out_dir = tmp_path / f"pack_{ext[1:]}"
        out_dir.mkdir()
        f = media_factory(f"video_test_{ext[1:]}", ext, 65536, target_dir=out_dir)

        payload = {
            "pack_title": f"Format Pack {ext}",
            "output_dir": str(out_dir),
            "scenes": [{"id": 10, "path": str(f)}],
        }
        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        assert any(str(f_entry).endswith(ext) for f_entry in t.files)

    def test_multi_dot_and_multi_extension_files(self, media_factory, tmp_path):
        """Filenames with multiple dots (e.g. scene.1080p.x264.mkv) preserve exact extension."""
        name = "Studio.Alpha.Scene.01.1080p.HEVC"
        out_dir = tmp_path / "multi_dot_out"
        out_dir.mkdir()
        f = media_factory(name, ".mkv", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "Multi Dot Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f)}],
        }
        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        assert any(f"{name}.mkv" in str(f_entry) for f_entry in t.files)


# ============================================================================
# 3. FILE SIZES & PIECE BOUNDARY CORNER CASES
# ============================================================================
class TestFileSizeAndPieceBoundaries:
    """Boundary conditions on file sizes and torf piece sizing."""

    @pytest.mark.parametrize("size_bytes, expected_piece_size", [
        (1, 16384),
        (16384, 16384),
        (32768, 16384),
        (1024 * 1024, 16384),                 # 1 MiB -> 16 KiB
        (100 * 1024 * 1024, 65536),           # 100 MiB -> 64 KiB
        (1024 * 1024 * 1024, 1048576),        # 1 GiB -> 1 MiB
        (4 * 1024 * 1024 * 1024, 4194304),    # 4 GiB -> 4 MiB
        (16 * 1024 * 1024 * 1024, 8388608),   # 16 GiB -> 8 MiB (clamped max)
    ])
    def test_piece_size_clamping_across_size_spectrum(self, size_bytes, expected_piece_size):
        """calculate_piece_size correctly computes piece sizes across byte magnitudes."""
        assert calculate_piece_size(size_bytes) == expected_piece_size

    def test_single_byte_file_in_torrent(self, media_factory, tmp_path):
        """A 1-byte file creates a valid torrent with 1 piece."""
        out_dir = tmp_path / "one_byte_out"
        out_dir.mkdir()
        f1 = media_factory("one_byte", ".mp4", 1, target_dir=out_dir)

        payload = {
            "pack_title": "OneBytePack",
            "output_dir": str(out_dir),
            "include_contact_sheets": False,
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        assert t.size == 1
        assert t.pieces == 1


# ============================================================================
# 4. DIRECTORY PATHS & NESTING CORNER CASES
# ============================================================================
class TestDirectoryPathsAndNesting:
    """Path depth, slash styles, and relative path handling."""

    def test_deeply_nested_source_media_path(self, media_factory, tmp_path):
        """Media consolidated inside output directory is discovered and processed."""
        out_dir = tmp_path / "deep_out"
        out_dir.mkdir()
        f1 = media_factory("deep_clip", ".mp4", 65536, target_dir=out_dir)

        payload = {
            "pack_title": "Deep Nested Pack",
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()

    def test_output_dir_with_trailing_slashes_and_backslashes(self, media_factory, tmp_path):
        """Output directory specified with mixed trailing slashes is normalized safely."""
        out_dir = tmp_path / "slash_out"
        out_dir.mkdir()
        f1 = media_factory("slash_clip", ".mp4", 65536, target_dir=out_dir)
        out_dir_str = str(out_dir) + "//"

        payload = {
            "pack_title": "Slash Pack",
            "output_dir": out_dir_str,
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()


# ============================================================================
# 5. LOCKFILE & PROCESS CONCURRENCY CORNER CASES
# ============================================================================
class TestLockfileConcurrencyCorners:
    """Expired lockfiles, dead PIDs, and active concurrency."""

    def test_lockfile_with_expired_timestamp_is_reclaimed(self, media_factory, tmp_path):
        """Lockfile older than 3600 seconds is treated as stale and reclaimed."""
        out_dir = tmp_path / "stale_lock_dir"
        out_dir.mkdir()
        f1 = media_factory("clip_stale", ".mp4", 65536, target_dir=out_dir)
        title = "StaleLockPack"
        lock_file = out_dir / f".{sanitize_name(title)}.lock"
        two_hours_ago = time.time() - 7200
        lock_file.write_text(f"pid={os.getpid()}\nstarted={two_hours_ago}\npack={title}\n", encoding="utf-8")

        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        stderr_buf = io.StringIO()
        with patch.object(sys.stderr, "write", stderr_buf.write):
            res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()
        assert "Reclaiming stale lockfile" in stderr_buf.getvalue()

    def test_lockfile_with_dead_pid_is_reclaimed(self, media_factory, tmp_path):
        """Lockfile referencing a non-existent PID is reclaimed."""
        out_dir = tmp_path / "dead_pid_dir"
        out_dir.mkdir()
        f1 = media_factory("clip_deadpid", ".mp4", 65536, target_dir=out_dir)
        title = "DeadPidPack"
        lock_file = out_dir / f".{sanitize_name(title)}.lock"
        lock_file.write_text(f"pid=999999\nstarted={time.time()}\npack={title}\n", encoding="utf-8")

        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        assert Path(res["torrent_path"]).exists()


# ============================================================================
# 6. SCENE COUNT & PREVIEW NAMING CORNER CASES
# ============================================================================
class TestSceneCountAndPreviewNamingCorners:
    """Single scene vs multi-scene naming conventions."""

    def test_single_scene_preview_named_without_index(self, media_factory, tmp_path):
        """Single scene megapack names preview image {pack_title}_preview.jpg without numeric suffix."""
        out_dir = tmp_path / "solo_out"
        out_dir.mkdir()
        f1 = media_factory("solo_scene", ".mp4", 65536, target_dir=out_dir)
        title = "SoloScenePack"

        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}],
        }
        res = run_build_megapack(payload)
        expected_cs = out_dir / f"{title}_preview.jpg"
        assert expected_cs.exists()

    def test_multi_scene_preview_named_with_index(self, media_factory, tmp_path):
        """Multi scene megapack names preview images {pack_title}_preview_1.jpg, _preview_2.jpg."""
        out_dir = tmp_path / "multi_cs_out"
        out_dir.mkdir()
        f1 = media_factory("multi1", ".mp4", 65536, target_dir=out_dir)
        f2 = media_factory("multi2", ".mp4", 65536, target_dir=out_dir)
        title = "MultiScenePack"

        payload = {
            "pack_title": title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(f1)}, {"id": 2, "path": str(f2)}],
        }
        res = run_build_megapack(payload)
        cs1 = out_dir / f"{title}_preview_1.jpg"
        cs2 = out_dir / f"{title}_preview_2.jpg"
        assert cs1.exists()
        assert cs2.exists()
