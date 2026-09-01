"""
Milestone 3 — Challenger 2 Adversarial Verification Suite.
Specialized empirical tests covering:
1. Foreign File Rejection & Target Directory Cleanliness Pre-Validation.
2. Basename Collision Pre-Validation & Blocking before moveFiles/Build.
3. Empty Payloads, Zero Scenes, and Non-Existent Path Handling.
4. Preserving Media Basenames & Extensions without Staging.
5. Stdin / CLI Native Process Protocol Fault Injection.
"""

import os
import sys
import io
import json
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

PLUGIN_DIR = ROOT_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task as task_module
from task import (
    sanitize_name,
    validate_pack_files_present,
    run_probe_files,
    run_build_megapack,
    parse_input_payload,
    emit_progress,
)
from empornium_megapack.models import MoveFilesRequest, MoveFilesResponse
from empornium_megapack.review import PackService
import torf


def _create_dummy_video(path: Path, size_bytes: int = 1024 * 64) -> Path:
    """Helper to create dummy media file with predictable content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SAMPLE_VIDEO_DATA_" + b"\x00" * (size_bytes - 18))
    return path


# ============================================================================
# 1. PACK-FILE-PRESENCE PRE-VALIDATION TESTS (replaces foreign-file rejection, T3)
# ============================================================================

class TestPackFilePresence:
    """Adversarial stress-testing of pack-file-presence pre-validation.

    OLD→NEW (T3): the former TestForeignFileRejection pinned the deleted
    foreign-file scan. Presence validation ignores unrelated entries; only a
    missing expected primary raises."""

    def test_direct_presence_validation_ignores_foreign_text_file(self, tmp_path):
        """OLD→NEW: unrelated.txt formerly raised "contains 1 foreign file";
        it is now ignored — only the expected primaries matter."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = target_dir / "Scene01.mp4"
        _create_dummy_video(media_file)

        foreign_file = target_dir / "unrelated.txt"
        foreign_file.write_text("unrelated stray content", encoding="utf-8")

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )  # must not raise

    def test_direct_presence_validation_ignores_extra_video(self, tmp_path):
        """OLD→NEW: extra_video.mp4 formerly raised "contains 1 foreign file";
        it is now ignored."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = target_dir / "Scene01.mp4"
        _create_dummy_video(media_file)

        extra_video = target_dir / "extra_video.mp4"
        _create_dummy_video(extra_video)

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )  # must not raise

    def test_direct_presence_validation_ignores_nested_foreign_folder(self, tmp_path):
        """OLD→NEW: nested foreign directories formerly raised "foreign file";
        they are now ignored."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = target_dir / "Scene01.mp4"
        _create_dummy_video(media_file)

        nested_dir = target_dir / "foreign_nested_folder"
        nested_dir.mkdir()
        (nested_dir / "nested_file.bin").write_bytes(b"data")

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )  # must not raise

    def test_direct_presence_validation_artifacts_and_contact_sheets_ignored(self, tmp_path):
        """OLD→NEW: artifacts and Contact Sheets formerly needed allowlist
        entries to pass; the allowlist is deleted — nothing is scanned."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = target_dir / "Scene01.mp4"
        _create_dummy_video(media_file)

        (target_dir / ".TestPack.lock").write_text("pid=123\n", encoding="utf-8")
        (target_dir / "TestPack.torrent").write_bytes(b"d8:announce...")
        (target_dir / "TestPack_manifest.json").write_text("{}", encoding="utf-8")
        (target_dir / "TestPack_bbcode.txt").write_text("[b]BBCode[/b]", encoding="utf-8")
        (target_dir / "TestPack_preview_1.jpg").write_bytes(b"\xff\xd8\xff")
        cs_dir = target_dir / "Contact Sheets"
        cs_dir.mkdir()
        (cs_dir / "sheet1.jpg").write_bytes(b"\xff\xd8\xff")

        validate_pack_files_present(
            str(target_dir),
            expected_primary_paths=[str(media_file)],
        )  # must not raise

    def test_direct_presence_validation_missing_primary_raises(self, tmp_path):
        """The refusal that remains: a declared primary absent from under the
        dir raises, naming the exact path with the Consolidate hint."""
        target_dir = tmp_path / "TargetPack"
        target_dir.mkdir()
        media_file = target_dir / "Scene01.mp4"
        _create_dummy_video(media_file)

        with pytest.raises(RuntimeError, match="missing from") as exc_info:
            validate_pack_files_present(
                str(target_dir),
                expected_primary_paths=[str(media_file), str(target_dir / "absent.mp4")],
            )
        assert "absent.mp4" in str(exc_info.value)
        assert "Run Consolidate or add the missing files to the seed directory" in str(exc_info.value)

    def test_build_refused_before_torrent_creation_with_missing_primary(self, tmp_path):
        """OLD→NEW: was test_build_refused_before_torrent_creation_with_stray_files
        (stray unrelated.txt in pack_dir → "contains .* foreign file", no
        torrent). Strays no longer refuse; a MISSING primary refuses before any
        artifact is written."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        pack_title = "StrayTestPack"
        pack_dir = output_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        media_file = pack_dir / "ValidScene.mp4"
        _create_dummy_video(media_file)

        # Declared primary that never landed in pack_dir
        missing_file = pack_dir / "MissingScene.mp4"

        payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [
                {"id": 1, "path": str(media_file)},
                {"id": 2, "path": str(missing_file)},
            ],
        }

        with pytest.raises(RuntimeError, match="missing from") as exc_info:
            run_build_megapack(payload)

        assert "MissingScene.mp4" in str(exc_info.value)
        # Verify torrent file was NEVER created
        torrent_path = output_dir / "StrayTestPack.torrent"
        assert not torrent_path.exists()
        # Verify manifest was NEVER written
        manifest_path = output_dir / "StrayTestPack_manifest.json"
        assert not manifest_path.exists()

    def test_build_succeeds_with_nested_foreign_folder(self, tmp_path):
        """OLD→NEW: was test_build_refused_with_nested_foreign_folder (foreign
        nested subdirectory in pack_dir → "foreign file" refusal, no torrent).
        The scan is deleted — the build succeeds and the folder is untouched."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        pack_title = "NestedForeignPack"
        pack_dir = output_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        media_file = pack_dir / "ValidScene.mp4"
        _create_dummy_video(media_file)

        # Foreign nested directory inside pack_dir: ignored
        foreign_folder = pack_dir / "UnexpectedFolder"
        foreign_folder.mkdir()
        (foreign_folder / "file.dat").write_bytes(b"dat")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(output_dir),
            "scenes": [{"id": 1, "path": str(media_file)}],
        }

        res = run_build_megapack(payload)
        assert res["status"] == "success"
        assert (foreign_folder / "file.dat").read_bytes() == b"dat"


# ============================================================================
# 2. BASENAME COLLISION PRE-VALIDATION & BLOCKING TESTS
# ============================================================================

class TestBasenameCollisionPreValidation:
    """Adversarial testing of collision pre-validation before moveFiles and torrent hashing."""

    def test_duplicate_basenames_blocked_before_move_and_build(self, tmp_path):
        """run_build_megapack hard-blocks when duplicate basenames exist across scenes."""
        dir1 = tmp_path / "StudioA"
        dir2 = tmp_path / "StudioB"
        dir1.mkdir()
        dir2.mkdir()

        # Two distinct scenes in different folders but identical basename 'scene01.mp4'
        f1 = dir1 / "scene01.mp4"
        f2 = dir2 / "scene01.mp4"
        _create_dummy_video(f1)
        _create_dummy_video(f2)

        output_dir = tmp_path / "ConsolidatedOutput"

        payload = {
            "pack_title": "CollisionPack",
            "output_dir": str(output_dir),
            "scenes": [
                {"id": 101, "title": "Scene 101", "path": str(f1)},
                {"id": 102, "title": "Scene 102", "path": str(f2)},
            ],
        }

        stderr_buf = io.StringIO()
        with patch.object(sys, "stderr", stderr_buf):
            with pytest.raises(RuntimeError, match="Basename collision detected") as exc_info:
                run_build_megapack(payload)

        err_msg = str(exc_info.value)
        assert "scene01.mp4" in err_msg
        assert "1 duplicates" in err_msg

        # Confirm stderr emitted native \x01e\x02 protocol error
        assert "\x01e\x02Basename collision detected" in stderr_buf.getvalue()

        # Confirm no artifacts were written to output_dir
        assert not (output_dir / "CollisionPack.torrent").exists()
        assert not (output_dir / "CollisionPack_manifest.json").exists()

    def test_case_insensitive_basename_collision(self, tmp_path):
        """Case variations like 'Video.MP4' and 'video.mp4' must be caught as collisions."""
        dir1 = tmp_path / "Dir1"
        dir2 = tmp_path / "Dir2"
        dir1.mkdir()
        dir2.mkdir()

        f1 = dir1 / "FeatureFilm.MP4"
        f2 = dir2 / "featurefilm.mp4"
        _create_dummy_video(f1)
        _create_dummy_video(f2)

        payload = {
            "pack_title": "CaseCollisionPack",
            "output_dir": str(tmp_path / "Out"),
            "scenes": [
                {"id": 1, "path": str(f1)},
                {"id": 2, "path": str(f2)},
            ],
        }

        with pytest.raises(RuntimeError, match="Basename collision detected"):
            run_build_megapack(payload)

    def test_probe_files_identifies_multiple_collisions(self, tmp_path):
        """run_probe_files detects all duplicate basenames and reports duplicate_count."""
        dir_a = tmp_path / "A"
        dir_b = tmp_path / "B"
        dir_c = tmp_path / "C"
        for d in (dir_a, dir_b, dir_c):
            d.mkdir()

        f1 = _create_dummy_video(dir_a / "clip1.mp4")
        f2 = _create_dummy_video(dir_b / "clip1.mp4")
        f3 = _create_dummy_video(dir_a / "clip2.mkv")
        f4 = _create_dummy_video(dir_c / "clip2.mkv")
        f5 = _create_dummy_video(dir_a / "unique.avi")

        payload = {
            "target_dir": str(tmp_path / "Target"),
            "files": [str(f1), str(f2), str(f3), str(f4), str(f5)],
        }

        result = run_probe_files(payload)
        assert result["status"] == "success"
        assert result["duplicate_count"] == 2  # clip1.mp4 and clip2.mkv
        files = result["files"]
        assert files[0]["is_duplicate_name"] is True
        assert files[1]["is_duplicate_name"] is True
        assert files[2]["is_duplicate_name"] is True
        assert files[3]["is_duplicate_name"] is True
        assert files[4]["is_duplicate_name"] is False

    def test_move_files_safety_with_mocked_stash(self):
        """PackService.move_files validates parameters and never executes move if destination is empty."""
        mock_stash = MagicMock()
        service = PackService(stash=mock_stash)

        # Missing destination
        req = MoveFilesRequest(scene_ids=["101", "102"], destination_folder="")
        resp = service.move_files(req)
        assert resp.error_count == 2
        assert resp.moved_count == 0
        assert resp.errors[0].code == "missing_destination"
        mock_stash.move_files.assert_not_called()


# ============================================================================
# 3. EMPTY PAYLOADS, ZERO SCENES, & NON-EXISTENT PATHS TESTS
# ============================================================================

class TestEmptyAndNonExistentInputs:
    """Adversarial stress-testing of empty inputs, zero scenes, and missing paths."""

    def test_empty_payload_dict_refused(self, tmp_path):
        """Empty payload dict {} raises RuntimeError and writes no torrent or manifest."""
        out_dir = tmp_path / "EmptyPacks"
        with pytest.raises(RuntimeError, match="No valid media files found in scenes payload"):
            run_build_megapack({"output_dir": str(out_dir)})

        assert not any(out_dir.glob("*.torrent"))
        assert not any(out_dir.glob("*_manifest.json"))

    def test_none_payload_refused(self, tmp_path):
        """None payload raises RuntimeError without uncaught AttributeError."""
        with pytest.raises(RuntimeError, match="No valid media files found"):
            run_build_megapack(None)

    def test_zero_scenes_list_refused(self, tmp_path):
        """scenes=[] payload raises RuntimeError and produces no hollow artifacts."""
        out_dir = tmp_path / "ZeroScenes"
        payload = {
            "pack_title": "ZeroPack",
            "output_dir": str(out_dir),
            "scenes": [],
        }
        with pytest.raises(RuntimeError, match="No valid media files found in scenes payload"):
            run_build_megapack(payload)

        assert not any(out_dir.glob("*.torrent"))

    def test_all_nonexistent_scene_paths_refused(self, tmp_path):
        """When all specified scene paths do not exist on disk, build is refused."""
        out_dir = tmp_path / "NonExistentPacks"
        payload = {
            "pack_title": "GhostPack",
            "output_dir": str(out_dir),
            "scenes": [
                {"id": 1, "path": r"C:\fake\nonexistent_scene_1.mp4"},
                {"id": 2, "path": r"D:\ghost\nonexistent_scene_2.mkv"},
            ],
        }
        with pytest.raises(RuntimeError, match="No valid media files found in scenes payload"):
            run_build_megapack(payload)

        assert not any(out_dir.glob("*.torrent"))

    def test_mixed_valid_and_nonexistent_paths(self, tmp_path):
        """OLD→NEW (T3): was "only valid scenes are included in pack" — a
        declared-but-nonexistent scene was silently dropped and the build
        succeeded with fewer files. Under presence validation every declared
        primary must exist under the pack dir, so the build now refuses,
        naming the missing path, and writes no torrent."""
        out_dir = tmp_path / "MixedPacks"
        out_dir.mkdir(parents=True, exist_ok=True)
        pack_title = "MixedPack"
        pack_dir = out_dir / sanitize_name(pack_title)
        pack_dir.mkdir(parents=True, exist_ok=True)
        valid_media = pack_dir / "ValidScene.mp4"
        _create_dummy_video(valid_media)

        payload = {
            "pack_title": pack_title,
            "output_dir": str(out_dir),
            "scenes": [
                {"id": 1, "path": str(valid_media)},
                {"id": 2, "path": r"C:\fake\missing_scene.mp4"},
            ],
        }

        with pytest.raises(RuntimeError, match="missing from") as exc_info:
            run_build_megapack(payload)
        assert "missing_scene.mp4" in str(exc_info.value)
        assert not any(out_dir.glob("*.torrent"))

    def test_malformed_scenes_entries_handled_gracefully(self, tmp_path):
        """Malformed entries (None, empty dict, string with missing file) handled safely."""
        payload = {
            "pack_title": "MalformedPack",
            "output_dir": str(tmp_path / "MalformedOut"),
            "scenes": [
                None,
                "",
                {},
                {"path": ""},
                {"files": [None, ""]},
                {"file_paths": []},
            ],
        }

        with pytest.raises(RuntimeError, match="No valid media files found in scenes payload"):
            run_build_megapack(payload)


# ============================================================================
# 4. MEDIA BASENAME & EXTENSION PRESERVATION TESTS (D1 ARCHITECTURE)
# ============================================================================

class TestMediaPreservationAndInPlaceSeeding:
    """Verifies that non-.mp4 files retain original basenames/extensions and are seedable in-place."""

    def test_heterogeneous_extensions_preserved_in_place(self, tmp_path):
        """Media files with .mkv, .avi, .wmv, .mp4 keep their exact names and extensions."""
        target_dir = tmp_path / "DirectConsolidatedPack"
        target_dir.mkdir()
        pack_title = "MultiFormat Megapack 2026"
        pack_dir = target_dir / sanitize_name(pack_title)
        pack_dir.mkdir()

        mkv_file = _create_dummy_video(pack_dir / "Feature_Scene.1080p.MKV")
        avi_file = _create_dummy_video(pack_dir / "Legacy_Clip.AVI")
        wmv_file = _create_dummy_video(pack_dir / "Archive_Video.WMV")
        mp4_file = _create_dummy_video(pack_dir / "Standard_Clip.mp4")

        payload = {
            "pack_title": pack_title,
            "output_dir": str(target_dir),
            "scenes": [
                {"id": 1, "path": str(mkv_file)},
                {"id": 2, "path": str(avi_file)},
                {"id": 3, "path": str(wmv_file)},
                {"id": 4, "path": str(mp4_file)},
            ],
        }

        res = run_build_megapack(payload)
        assert res["status"] == "success"

        # Verify on disk: files still exist with EXACT source names and extensions
        assert mkv_file.exists()
        assert avi_file.exists()
        assert wmv_file.exists()
        assert mp4_file.exists()

        # Verify torrent file indices
        t = torf.Torrent.read(res["torrent_path"])
        torrent_filenames = [os.path.basename(str(f)) for f in t.files]
        assert "Feature_Scene.1080p.MKV" in torrent_filenames
        assert "Legacy_Clip.AVI" in torrent_filenames
        assert "Archive_Video.WMV" in torrent_filenames
        assert "Standard_Clip.mp4" in torrent_filenames

        # Verify manifest indices
        with open(res["manifest_path"], "r", encoding="utf-8") as mf:
            manifest_data = json.load(mf)
        assert manifest_data["scene_count"] == 4

        # Verify pieces match on-disk data (100% seedable in-place)
        assert t.verify(pack_dir) is True


# ============================================================================
# 5. SUBPROCESS CLI RUNNER PROTOCOL ADVERSARIAL TESTS
# ============================================================================

class TestSubprocessCliFaultProtocols:
    """Adversarial testing of task.py executed as native Stash subprocess via stdin."""

    def test_cli_missing_file_rejection(self, tmp_path):
        """OLD→NEW (T3): was test_cli_foreign_file_rejection — task.py as
        subprocess exited 1 with \\x01e\\x02 on foreign files. Foreign-file
        refusal is deleted; the subprocess now exits 1 with \\x01e\\x02 when a
        declared pack primary is missing from the pack dir."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        pack_title = "CliForeignPack"
        pack_dir = out_dir / sanitize_name(pack_title)
        pack_dir.mkdir()
        media_file = _create_dummy_video(pack_dir / "Scene.mp4")
        missing_file = pack_dir / "Absent.mp4"

        payload = {
            "task_name": "BuildMegapack",
            "args": [
                {"key": "mode", "value": "build"},
                {"key": "payload", "value": json.dumps({
                    "pack_title": pack_title,
                    "output_dir": str(out_dir),
                    "scenes": [
                        {"id": 1, "path": str(media_file)},
                        {"id": 2, "path": str(missing_file)},
                    ],
                })},
            ],
        }

        task_py = ROOT_DIR / "plugin" / "task.py"
        proc = subprocess.run(
            [sys.executable, str(task_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert proc.returncode != 0
        assert "\x01e\x02" in proc.stderr
        assert "missing from" in proc.stderr.lower()
        assert "Absent.mp4" in proc.stderr
        assert not (out_dir / "CliForeignPack.torrent").exists()

    def test_cli_basename_collision_rejection(self, tmp_path):
        """task.py as subprocess exits code 1 with \\x01e\\x02 message on duplicate basenames."""
        d1 = tmp_path / "A"
        d2 = tmp_path / "B"
        d1.mkdir()
        d2.mkdir()
        f1 = _create_dummy_video(d1 / "duplicate.mp4")
        f2 = _create_dummy_video(d2 / "duplicate.mp4")

        payload = {
            "task_name": "BuildMegapack",
            "args": [
                {"key": "mode", "value": "build"},
                {"key": "payload", "value": json.dumps({
                    "pack_title": "CliCollisionPack",
                    "output_dir": str(tmp_path / "Out"),
                    "scenes": [
                        {"id": 1, "path": str(f1)},
                        {"id": 2, "path": str(f2)},
                    ],
                })},
            ],
        }

        task_py = ROOT_DIR / "plugin" / "task.py"
        proc = subprocess.run(
            [sys.executable, str(task_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert proc.returncode != 0
        assert "\x01e\x02Basename collision detected" in proc.stderr

    def test_cli_empty_scenes_rejection(self, tmp_path):
        """task.py as subprocess exits code 1 when scenes payload is empty."""
        payload = {
            "task_name": "BuildMegapack",
            "args": [
                {"key": "mode", "value": "build"},
                {"key": "payload", "value": json.dumps({
                    "pack_title": "CliEmptyPack",
                    "output_dir": str(tmp_path / "Out"),
                    "scenes": [],
                })},
            ],
        }

        task_py = ROOT_DIR / "plugin" / "task.py"
        proc = subprocess.run(
            [sys.executable, str(task_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert proc.returncode != 0
        assert "\x01e\x02No valid media files found in scenes payload" in proc.stderr
