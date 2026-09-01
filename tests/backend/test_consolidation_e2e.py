"""
End-to-End Consolidation Integration Suite.
Directly verifies the core acceptance criteria against production code paths:
1. Consolidated media preserves original basename and original extension (.mkv, .avi, .wmv, .mp4).
2. Basename collision (duplicate_count > 0) blocks consolidation and build before destructive operations.
3. Unconsolidated scenes (media outside output_dir) cause immediate refusal without guessing commonpath.
4. Stray unrelated file in destination folder causes pre-validation refusal before torrent generation.
5. Zero temporary staging directories created during build (direct in-place torrent creation).
6. Empty input payload is rejected with error without writing hollow torrents or artifacts.
7. Clean importability of domain package from arbitrary cwds and plugin directories.
"""

import sys
import os
import io
import json
import time
import shutil
import tempfile
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock
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

import task as task_module
from task import (
    sanitize_name,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
)


def _resolve_domain():
    try:
        import empornium_megapack as dm
        return dm
    except ImportError:
        import app as dm
        return dm


class TestConsolidationCoreAcceptanceCriteria:
    """Core acceptance criteria verification for in-place consolidation."""

    # ------------------------------------------------------------------------
    # CRITERION 1: Preserving original basename and extension (.mkv, .avi, .wmv, .mp4)
    # ------------------------------------------------------------------------
    @pytest.mark.parametrize("ext, base_title", [
        (".mkv", "Original_Basename_Alpha"),
        (".avi", "Original_Basename_Beta"),
        (".wmv", "Original_Basename_Gamma"),
        (".mp4", "Original_Basename_Delta"),
    ])
    def test_criterion_1_preserves_basename_and_extension(self, tmp_path, ext, base_title):
        """Media files preserve original basename and extension without renaming or .mp4 override."""
        out_dir = tmp_path / f"dest_{ext[1:]}"
        out_dir.mkdir()
        pack_title = f"Preserve Test {ext}"
        pack_dir = out_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        # In-place consolidated media lives directly in pack_dir
        media_file = pack_dir / f"{base_title}{ext}"
        media_file.write_bytes(b"Sample media bytes for preservation check" * 100)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [{"id": 1, "path": str(media_file)}],
        }

        res = run_build_megapack(payload)
        t = torf.Torrent.read(res["torrent_path"])
        files_in_torrent = [str(f) for f in t.files]

        expected_filename = f"{base_title}{ext}"
        assert any(expected_filename in f for f in files_in_torrent)
        assert any(f.endswith(ext) for f in files_in_torrent)
        # Verify file on disk is untouched and retains original name and content
        assert media_file.exists()
        assert media_file.name == f"{base_title}{ext}"
        assert bool(t.verify(str(pack_dir))) is True

    # ------------------------------------------------------------------------
    # CRITERION 2: Basename collision blocks consolidation and build
    # ------------------------------------------------------------------------
    def test_criterion_2_basename_collision_blocks_consolidation_and_build(self, tmp_path):
        """Basename collision (duplicate_count > 0) blocks consolidation and build via production code."""
        folder1 = tmp_path / "f1"
        folder2 = tmp_path / "f2"
        target_dir = tmp_path / "Target"
        folder1.mkdir()
        folder2.mkdir()
        target_dir.mkdir()

        f1 = folder1 / "Colliding_Name.mp4"
        f2 = folder2 / "Colliding_Name.mp4"
        f1.write_bytes(b"content 1")
        f2.write_bytes(b"content 2")

        probe_payload = {
            "target_dir": str(target_dir),
            "files": [
                {"scene_id": 10, "path": str(f1)},
                {"scene_id": 20, "path": str(f2)},
            ],
        }

        # 1. ProbeFiles correctly detects collision
        probe_res = run_probe_files(probe_payload)
        assert probe_res["duplicate_count"] == 1
        assert any(f["is_duplicate_name"] for f in probe_res["files"])

        # 2. BuildMegapack production entrypoint hard-fails on duplicate basenames
        build_payload = {
            "pack_title": "Collision Pack",
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 10, "path": str(f1)},
                {"id": 20, "path": str(f2)},
            ],
        }
        with pytest.raises(RuntimeError, match="Basename collision detected"):
            run_build_megapack(build_payload)

        # Source files remain untouched
        assert f1.exists()
        assert f2.exists()
        # Zero torrents generated
        assert len(list(target_dir.glob("*.torrent"))) == 0

    # ------------------------------------------------------------------------
    # CRITERION 3: Unconsolidated scenes cause immediate refusal (no commonpath)
    # ------------------------------------------------------------------------
    def test_criterion_3_unconsolidated_scenes_cause_immediate_refusal(self, tmp_path):
        """Build refuses to execute when media is not consolidated into pack_dir, preventing commonpath leaking."""
        lib_a = tmp_path / "lib" / "A"
        lib_b = tmp_path / "lib" / "B"
        dest_dir = tmp_path / "Packs" / "MyPack"
        lib_a.mkdir(parents=True)
        lib_b.mkdir(parents=True)
        dest_dir.mkdir(parents=True)

        scene_a = lib_a / "scene_a.mkv"
        scene_b = lib_b / "scene_b.mkv"
        unrelated = tmp_path / "lib" / "private_video.mkv"
        scene_a.write_bytes(b"scene a")
        scene_b.write_bytes(b"scene b")
        unrelated.write_bytes(b"unrelated private file")

        payload = {
            "pack_title": "Unconsolidated Pack",
            "output_dir": str(dest_dir),
            "scenes": [
                {"id": 1, "path": str(scene_a)},
                {"id": 2, "path": str(scene_b)},
            ],
        }

        # Must refuse with clear message rather than torrenting commonpath (tmp_path / lib)
        with pytest.raises(RuntimeError, match=r"Pack directory.*does not exist|Scenes are not consolidated into pack directory"):
            run_build_megapack(payload)

        # Confirm zero torrent files were created
        assert len(list(dest_dir.glob("*.torrent"))) == 0
        assert len(list((tmp_path / "lib").glob("*.torrent"))) == 0

    # ------------------------------------------------------------------------
    # CRITERION 4: Stray unrelated file in destination is ignored (T3)
    # ------------------------------------------------------------------------
    def test_criterion_4_stray_unrelated_file_causes_refusal(self, tmp_path):
        """OLD→NEW (T3): was "stray unrelated file inside pack_dir causes
        pre-validation refusal" (RuntimeError "foreign file"). The foreign-file
        scan is deleted — unrelated files inside the pack dir are ignored and
        the build succeeds; only MISSING pack primaries block. Parent-level
        strays were already ignored and still are."""
        target_dir = tmp_path / "Target_With_Stray"
        target_dir.mkdir()
        pack_title = "Cleanliness Test Pack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()

        # Expected pack media file located in pack_dir
        pack_media = pack_dir / "valid_pack_scene.mp4"
        pack_media.write_bytes(b"valid scene bytes" * 50)

        # Stray unrelated foreign file placed in parent output_dir (ignored)
        parent_stray = target_dir / "unrelated_parent_movie.mkv"
        parent_stray.write_bytes(b"unrelated foreign file bytes" * 50)

        # Stray unrelated foreign file placed inside pack_dir (now ignored too)
        pack_stray = pack_dir / "unrelated_pack_movie.mkv"
        pack_stray.write_bytes(b"unrelated foreign file bytes" * 50)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [{"id": 1, "path": str(pack_media)}],
        }

        # Build succeeds with stray files present (presence validation only
        # checks the declared primaries); strays are untouched
        res = run_build_megapack(payload)
        assert res["status"] == "success"
        assert os.path.exists(res["torrent_path"])
        assert pack_stray.read_bytes() == b"unrelated foreign file bytes" * 50
        assert parent_stray.exists()

    # ------------------------------------------------------------------------
    # CRITERION 5: Zero temporary staging directories created during build
    # ------------------------------------------------------------------------
    def test_criterion_5_zero_temporary_staging_directories_during_in_place_build(self, tmp_path):
        """In-place direct torrent creation builds directly over pack_dir with zero temp staging."""
        target_dir = tmp_path / "Direct_Seeding_Dir"
        target_dir.mkdir()
        pack_title = "Direct Seeding Pack"
        pack_dir = target_dir / pack_title
        pack_dir.mkdir()

        f1 = pack_dir / "Scene_A.mp4"
        f2 = pack_dir / "Scene_B.mkv"
        f1.write_bytes(b"Scene A bytes" * 100)
        f2.write_bytes(b"Scene B bytes" * 100)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 1, "path": str(f1)},
                {"id": 2, "path": str(f2)},
            ],
        }

        res = run_build_megapack(payload)
        torrent_path = res["torrent_path"]
        assert os.path.exists(torrent_path)

        t = torf.Torrent.read(torrent_path)
        # Direct in-place verification: torrent matches the destination folder perfectly
        assert t.verify(str(pack_dir)) is True

        # Verify original files remain intact and accessible at their original locations
        assert f1.exists()
        assert f2.exists()

    # ------------------------------------------------------------------------
    # CRITERION 6: Empty input payload is rejected without writing hollow torrents
    # ------------------------------------------------------------------------
    def test_criterion_6_empty_payload_rejected_no_hollow_artifacts(self, tmp_path):
        """Empty input payload is rejected with error without writing hollow torrents or artifacts."""
        out_dir = tmp_path / "empty_test_out"
        out_dir.mkdir()

        empty_payloads = [
            {},
            {"scenes": []},
            {"scenes": [{"id": 1, "path": str(tmp_path / "non_existent.mp4")}]},
        ]

        for payload in empty_payloads:
            payload["output_dir"] = str(out_dir)
            with pytest.raises(RuntimeError, match="No valid media files found"):
                run_build_megapack(payload)

        # Confirm zero .torrent or manifest files exist in out_dir
        assert len(list(out_dir.glob("*.torrent"))) == 0
        assert len(list(out_dir.glob("*_manifest.json"))) == 0
        assert len(list(out_dir.glob("*_bbcode.txt"))) == 0

    # ------------------------------------------------------------------------
    # CRITERION 7: Clean importability of empornium_megapack / domain package
    # ------------------------------------------------------------------------
    def test_criterion_7_clean_importability_from_arbitrary_cwd(self, tmp_path):
        """Clean importability of domain package from arbitrary working directories."""
        cwd_original = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            dm = _resolve_domain()
            assert dm is not None
        finally:
            os.chdir(cwd_original)

