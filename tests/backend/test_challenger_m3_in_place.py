"""
Milestone 3 Empirical Challenger Automated Pytest Suite: In-Place Seeding & Real File Artifacts.

Tests:
1. Heterogeneous media formats (.mkv, .avi, .wmv, .mp4, and multi-format combinations).
2. Deep inspection of generated .torrent using torf.Torrent.read:
   - 100% matched pieces against on-disk files.
   - Non-empty piece hashes.
   - Exact file paths matching on-disk layout.
   - Exact basenames and extensions preserved without renaming.
3. Zero temporary directories created (tempfile.mkdtemp / megapack_torf_*).
4. Destination cleanliness pre-validation (foreign files/dirs rejected, allowed artifacts permitted).
5. Basename collision pre-validation before moveFiles/build.
6. Unicode & special character handling.
7. Contact sheet inclusion vs exclusion in torrent.
8. Piece size policy calculations across boundaries.
"""

import sys
import os
import io
import time
import shutil
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

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

import torf
import task as task_module
from task import (
    run_build_megapack,
    run_probe_files,
    validate_pack_files_present,
    sanitize_name,
)
from deepseek_megapack.torrents import (
    piece_size_for,
    calculate_piece_size,
    create_torrent,
    MIN_PIECE_SIZE,
    MAX_PIECE_EXPONENT,
)


class TestM3PieceSizeCalculationPolicy:
    """Piece size exponent and bounds calculation stress tests."""

    @pytest.mark.parametrize("input_bytes, expected_piece_size", [
        (0, 16384),
        (-100, 16384),
        (10 * 1024, 16384),
        (16 * 1024, 16384),
        (16 * 1024 * 1024, 16384),
        (32 * 1024 * 1024, 32768),
        (1024 * 1024 * 1024, 1048576),
        (2 * 1024 * 1024 * 1024, 2097152),
        (4 * 1024 * 1024 * 1024, 4194304),
        (8 * 1024 * 1024 * 1024, 8388608),
        (100 * 1024 * 1024 * 1024, 8388608),
    ])
    def test_piece_size_policy_clamping_and_calculation(self, input_bytes, expected_piece_size):
        assert piece_size_for(input_bytes) == expected_piece_size


class TestM3InPlaceHeterogeneousTorrents:
    """In-place torrent verification for heterogeneous non-mp4 media."""

    def test_heterogeneous_pack_inplace_and_zero_temp_dirs(self, tmp_path):
        target_dir = tmp_path / "heterogeneous_pack"
        target_dir.mkdir()
        pack_title = "Heterogeneous Multi Format Pack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()

        f_mkv = pack_dir / "Feature_Film_HD.mkv"
        f_avi = pack_dir / "Classic_Scene_Rip.avi"
        f_wmv = pack_dir / "Web_Broadcast_Stream.wmv"
        f_mp4 = pack_dir / "Modern_4K_Release.mp4"

        f_mkv.write_bytes(b"MKV_HEADER_MAGIC_BYTES_" + b"A" * 65536)
        f_avi.write_bytes(b"RIFF_AVI_STREAM_BYTES_" + b"B" * 49152)
        f_wmv.write_bytes(b"ASF_WMV_PACKET_BYTES_" + b"C" * 32768)
        f_mp4.write_bytes(b"FTYP_MP4_ATOM_BYTES_" + b"D" * 81920)

        total_expected_bytes = f_mkv.stat().st_size + f_avi.stat().st_size + f_wmv.stat().st_size + f_mp4.stat().st_size

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 101, "title": "Feature 1", "file_paths": [str(f_mkv)]},
                {"id": 102, "title": "Feature 2", "file_paths": [str(f_avi)]},
                {"id": 103, "title": "Feature 3", "file_paths": [str(f_wmv)]},
                {"id": 104, "title": "Feature 4", "file_paths": [str(f_mp4)]},
            ],
            "trackers": ["http://tracker.empornium.sx:2710/passkey123/announce"],
            "include_contact_sheets": False,
        }

        mkdtemp_calls = []
        original_mkdtemp = tempfile.mkdtemp

        def spied_mkdtemp(*args, **kwargs):
            res = original_mkdtemp(*args, **kwargs)
            mkdtemp_calls.append(res)
            return res

        with patch("tempfile.mkdtemp", side_effect=spied_mkdtemp):
            res = run_build_megapack(payload)

        assert res.get("status") == "success"
        assert len(mkdtemp_calls) == 0

        torrent_path = Path(res["torrent_path"])
        assert torrent_path.exists()

        t = torf.Torrent.read(torrent_path)
        assert t.name == pack_title
        assert t.size == total_expected_bytes
        assert t.pieces > 0
        assert t.piece_size >= 16384
        assert len(t.hashes) == t.pieces
        assert all(len(h) == 20 for h in t.hashes)

        files_in_torrent = [Path(f) for f in t.files]
        assert len(files_in_torrent) == 4
        assert any(f.name == "Feature_Film_HD.mkv" for f in files_in_torrent)
        assert any(f.name == "Classic_Scene_Rip.avi" for f in files_in_torrent)
        assert any(f.name == "Web_Broadcast_Stream.wmv" for f in files_in_torrent)
        assert any(f.name == "Modern_4K_Release.mp4" for f in files_in_torrent)

        # 100% matched pieces on disk
        assert bool(t.verify(str(pack_dir))) is True

    def test_contact_sheets_subfolder_inclusion(self, tmp_path):
        target_dir = tmp_path / "cs_pack"
        target_dir.mkdir()
        pack_title = "Pack With Contact Sheets"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()

        f_mkv = pack_dir / "Scene_A.mkv"
        f_mkv.write_bytes(b"SCENE_A_DATA" * 5000)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [{"id": 301, "path": str(f_mkv)}],
            "trackers": ["http://tracker.empornium.sx:2710/announce"],
            "include_contact_sheets": True,
        }

        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        t_files_str = [str(f).replace("\\", "/") for f in t.files]

        assert any("Scene_A.mkv" in f for f in t_files_str)
        assert any("Contact Sheets/" in f for f in t_files_str)
        assert bool(t.verify(str(pack_dir))) is True

    def test_unicode_and_special_characters_in_filenames(self, tmp_path):
        target_dir = tmp_path / "unicode_pack"
        target_dir.mkdir()
        pack_title = "Unicode & Symbols Megapack 2026"
        pack_dir = target_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        f_jp = pack_dir / "[Studio] 日本語 映画 (2026) [1080p].mkv"
        f_cy = pack_dir / "Русский Фильм 2026 (Оригинал).wmv"
        f_sp = pack_dir / "Special & Characters @ 100% Final [4K].avi"

        f_jp.write_bytes(b"JAPANESE_VIDEO_BYTES_" * 4000)
        f_cy.write_bytes(b"CYRILLIC_VIDEO_BYTES_" * 3000)
        f_sp.write_bytes(b"SPECIAL_CHARS_BYTES_" * 5000)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 401, "path": str(f_jp)},
                {"id": 402, "path": str(f_cy)},
                {"id": 403, "path": str(f_sp)},
            ],
            "trackers": ["http://tracker.empornium.sx:2710/announce"],
            "include_contact_sheets": False,
        }

        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        t_files_str = [str(f) for f in t.files]

        assert any("[Studio] 日本語 映画 (2026) [1080p].mkv" in f for f in t_files_str)
        assert any("Русский Фильм 2026 (Оригинал).wmv" in f for f in t_files_str)
        assert any("Special & Characters @ 100% Final [4K].avi" in f for f in t_files_str)
        assert bool(t.verify(str(pack_dir))) is True


class TestM3PreValidationGates:
    """Pre-validation gating tests: target directory cleanliness, collisions, empty payloads."""

    def test_foreign_file_cleanliness_prevalidation(self, tmp_path):
        """OLD→NEW (T3): was validate_target_directory_cleanliness gating —
        stray txt/mkv/subdir each raised "foreign file". That scan is deleted;
        validate_pack_files_present ignores unrelated entries entirely and only
        refuses when an expected primary is missing from under the dir."""
        target_dir = tmp_path / "cleanliness_test"
        target_dir.mkdir()

        media_file = target_dir / "Valid_Media.mp4"
        media_file.write_bytes(b"VALID_MEDIA" * 1000)

        # Present primary passes
        validate_pack_files_present(str(target_dir), [str(media_file)])

        # Stray text file is ignored (formerly refused)
        foreign_txt = target_dir / "unrelated_notes.txt"
        foreign_txt.write_text("Stray notes")
        validate_pack_files_present(str(target_dir), [str(media_file)])

        # Stray mkv file is ignored (formerly refused)
        foreign_mkv = target_dir / "unrelated_movie.mkv"
        foreign_mkv.write_bytes(b"MOVIE" * 100)
        validate_pack_files_present(str(target_dir), [str(media_file)])

        # Stray directory is ignored (formerly refused)
        foreign_sub = target_dir / "OtherAlbum"
        foreign_sub.mkdir()
        validate_pack_files_present(str(target_dir), [str(media_file)])

        # Contact Sheets directory ignored, as before
        cs_dir = target_dir / "Contact Sheets"
        cs_dir.mkdir()
        validate_pack_files_present(str(target_dir), [str(media_file)])

        # A missing primary is what refuses now
        with pytest.raises(RuntimeError, match="missing from"):
            validate_pack_files_present(str(target_dir), [str(target_dir / "absent.mp4")])

    def test_basename_collision_prevalidation_blocks_build(self, tmp_path):
        target_dir = tmp_path / "collision_test"
        target_dir.mkdir()

        subA = target_dir / "dirA"
        subB = target_dir / "dirB"
        subA.mkdir()
        subB.mkdir()

        f_a = subA / "Scene_01.mkv"
        f_b = subB / "Scene_01.mkv"
        f_a.write_bytes(b"SCENE_A" * 1000)
        f_b.write_bytes(b"SCENE_B" * 1000)

        payload = {
            "pack_title": "Collision Test Exact",
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 601, "path": str(f_a)},
                {"id": 602, "path": str(f_b)},
            ],
            "trackers": ["http://tracker.empornium.sx:2710/announce"],
        }

        with pytest.raises(RuntimeError, match="Basename collision detected"):
            run_build_megapack(payload)

    def test_empty_payload_and_nonexistent_files_rejected(self, tmp_path):
        target_dir = tmp_path / "empty_test"
        target_dir.mkdir()

        with pytest.raises(RuntimeError, match="No valid media files"):
            run_build_megapack({"pack_title": "Empty Pack", "output_dir": str(target_dir), "scenes": []})

        with pytest.raises(RuntimeError, match="No valid media files"):
            run_build_megapack({
                "pack_title": "Ghost Pack",
                "output_dir": str(target_dir),
                "scenes": [{"id": 701, "path": str(target_dir / "nonexistent.mp4")}],
            })

        assert len(list(target_dir.iterdir())) == 0
