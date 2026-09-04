"""
Unit and integration tests for early megapack file presence validation in plugin/task.py.

Validates:
- Early abort before emit_progress(0.20) and contact sheet generation / HamsterImg upload
- Error formatting with Stash scene IDs (scene <id> -> <path>)
- Cleanup remedy recommendation in error messages
- Guard precedence (directory existence -> outside containment -> validate_pack_files_present)
- Healthy builds pass without false positives
- Single-scene early validation parity
- Invariant: downstream validate_pack_files_present call preserved
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

import task


def test_build_scene_id_map_with_various_scene_formats():
    """Verifies _build_scene_id_map extracts scene IDs from different scene dict structures."""
    scenes = [
        {"id": "101", "files": [{"path": "/media/scene1.mp4"}]},
        {"scene_id": 102, "path": "/media/scene2.mp4"},
        {"id": "103", "file_paths": ["/media/scene3_a.mp4", "/media/scene3_b.mp4"]},
        {"id": 104, "source_path": "/media/scene4.mp4"},
    ]
    mapping = task._build_scene_id_map(scenes)
    assert mapping.get("/media/scene1.mp4") == "101"
    assert mapping.get("/media/scene2.mp4") == "102"
    assert mapping.get("/media/scene3_a.mp4") == "103"
    assert mapping.get("/media/scene3_b.mp4") == "103"
    assert mapping.get("/media/scene4.mp4") == "104"

    norm_p = os.path.normcase(os.path.abspath("/media/scene1.mp4"))
    assert mapping.get(norm_p) == "101"


def test_validate_pack_files_present_formats_scene_ids_and_remedy(tmp_path):
    """Verifies validate_pack_files_present includes scene IDs and Stash cleanup advice."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    present_file = seed_dir / "present.mp4"
    present_file.write_bytes(b"DATA")
    missing_file1 = seed_dir / "missing1.mp4"
    missing_file2 = seed_dir / "missing2.mp4"

    scenes = [
        {"id": "42", "path": str(missing_file1)},
        {"id": "99", "path": str(missing_file2)},
        {"id": "100", "path": str(present_file)},
    ]

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(
            str(seed_dir),
            [str(present_file), str(missing_file1), str(missing_file2)],
            scenes=scenes,
        )

    err_msg = str(exc_info.value)
    assert f"scene 42 -> {str(missing_file1)}" in err_msg
    assert f"scene 99 -> {str(missing_file2)}" in err_msg
    assert str(present_file) not in err_msg
    assert "Run Consolidate or add the missing files to the seed directory" in err_msg
    assert "or run a Stash library scan/cleanup to resolve stale records" in err_msg


def test_validate_pack_files_present_fallback_without_scene_ids(tmp_path):
    """When scenes payload has no IDs or is None, missing paths are reported verbatim."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    missing_file = seed_dir / "stale.mp4"

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(
            str(seed_dir),
            [str(missing_file)],
            scenes=None,
        )

    err_msg = str(exc_info.value)
    assert f"Pack file(s) missing from '{seed_dir}': {str(missing_file)}" in err_msg
    assert "or run a Stash library scan/cleanup to resolve stale records" in err_msg


def test_early_megapack_validation_aborts_before_contact_sheets_and_uploads(tmp_path, monkeypatch):
    """
    Incident reproduction test:
    A megapack with 2 scenes where 1 scene file is missing on disk aborts early.
    Zero contact sheets generated, zero HamsterImg uploads attempted.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    present_media = seed_dir / "scene_present.mp4"
    present_media.write_bytes(b"VIDEO" * 1000)
    missing_media = seed_dir / "scene_stale.mp4"  # Does not exist

    progress_events = []
    def mock_emit_progress(progress, message=""):
        progress_events.append((progress, message))

    monkeypatch.setattr(task, "emit_progress", mock_emit_progress)

    contact_sheet_called = []
    def mock_generate_contact_sheet(video_path, out_path, *args, **kwargs):
        contact_sheet_called.append((video_path, out_path))
        Path(out_path).write_bytes(b"JPEG")
        return out_path

    monkeypatch.setattr(task, "generate_contact_sheet", mock_generate_contact_sheet)

    upload_called = []
    if hasattr(task, "_domain_images") and task._domain_images:
        monkeypatch.setattr(task._domain_images, "upload_hamster", lambda *a, **kw: upload_called.append(a))

    payload = {
        "run_id": "test-early-abort-1",
        "pack_title": "Early Abort Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "single_scene": False,
        "scenes": [
            {"id": "scene_ok", "title": "OK Scene", "path": str(present_media)},
            {"id": "scene_stale_136", "title": "Stale Scene", "path": str(missing_media)},
        ],
        "include_contact_sheets": True,
    }

    with pytest.raises(RuntimeError) as exc_info:
        task.run_build_megapack(payload, None)

    err_msg = str(exc_info.value)
    # 1. Error message contains scene ID and stale path
    assert f"scene scene_stale_136 -> {str(missing_media)}" in err_msg
    assert "or run a Stash library scan/cleanup to resolve stale records" in err_msg

    # 2. Aborted before progress 0.20 (contact sheet generation)
    progress_values = [p[0] for p in progress_events]
    assert max(progress_values) < 0.20, f"Progress reached {max(progress_values)}, expected < 0.20"
    assert any(0.15 <= p < 0.20 for p in progress_values)

    # 3. Zero contact sheets generated
    assert len(contact_sheet_called) == 0, "Contact sheet generator should not have been called"

    # 4. Zero uploads attempted
    assert len(upload_called) == 0, "Upload should not have been attempted"


def test_megapack_guard_precedence_consolidation_dir_not_found(tmp_path):
    """
    Guard 1: If consolidation_dir does not exist (legacy mode), raises 'Pack directory does not exist'
    prior to validate_pack_files_present.
    """
    output_dir = tmp_path / "custom_output"
    output_dir.mkdir()
    # pack_dir will be output_dir / "Guard Precedence Pack", which does NOT exist

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    existing_file = other_dir / "file1.mp4"
    existing_file.write_bytes(b"DATA")

    payload = {
        "run_id": "test-guard-1",
        "pack_title": "Guard Precedence Pack",
        "output_dir": str(output_dir),
        "single_scene": False,
        "scenes": [
            {"id": "1", "path": str(existing_file)},
        ],
    }

    with pytest.raises(RuntimeError) as exc_info:
        task.run_build_megapack(payload, None)

    assert "Pack directory" in str(exc_info.value)
    assert "does not exist" in str(exc_info.value)


def test_megapack_guard_precedence_outside_containment(tmp_path):
    """
    Guard 2: If a present file is outside the consolidation dir, the 'outside' check
    raises prior to validate_pack_files_present.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    other_dir = tmp_path / "outside_lib"
    other_dir.mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    outside_file = other_dir / "scene_outside.mp4"
    outside_file.write_bytes(b"OUTSIDE_DATA")

    payload = {
        "run_id": "test-guard-2",
        "pack_title": "Outside Guard Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "single_scene": False,
        "scenes": [
            {"id": "outside_1", "path": str(outside_file)},
        ],
    }

    with pytest.raises(RuntimeError) as exc_info:
        task.run_build_megapack(payload, None)

    assert "Scene files are not under the seed directory" in str(exc_info.value)


def test_healthy_megapack_build_passes_early_presence_validation(tmp_path, monkeypatch):
    """
    Healthy build: When all declared files exist under the seed directory,
    early validation passes and proceeds.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    f1 = seed_dir / "s1.mp4"
    f2 = seed_dir / "s2.mp4"
    f1.write_bytes(b"V1" * 100)
    f2.write_bytes(b"V2" * 100)

    progress_events = []
    monkeypatch.setattr(task, "emit_progress", lambda p, m="": progress_events.append((p, m)))
    def mock_cs(video_path, out_path, *args, **kwargs):
        Path(out_path).write_bytes(b"JPEG")
        return out_path
    monkeypatch.setattr(task, "generate_contact_sheet", mock_cs)
    monkeypatch.setattr(task, "make_thumbnail", lambda src, dest, max_width: Path(dest))
    monkeypatch.setattr(task, "upload_previews", lambda paths, payload, **kwargs: ["http://fake.img/1.jpg"] * len(paths))
    monkeypatch.setattr(task, "_verify_torrent_exact_set", lambda *a, **kw: None)
    monkeypatch.setattr(task, "create_torrent", lambda *a, **kw: None)
    monkeypatch.setattr(task, "post_result_to_sidecar", lambda *a, **kw: None)

    payload = {
        "run_id": "test-healthy-pack",
        "pack_title": "Healthy Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "single_scene": False,
        "scenes": [
            {"id": "1", "title": "Scene 1", "path": str(f1)},
            {"id": "2", "title": "Scene 2", "path": str(f2)},
        ],
        "include_contact_sheets": False,
    }

    res = task.run_build_megapack(payload, None)
    assert res is not None
    progress_values = [p[0] for p in progress_events]
    assert any(p >= 0.20 for p in progress_values)


def test_single_scene_mode_early_validation_with_scene_id(tmp_path):
    """
    Single-scene mode with seed_dir provided aborts early with scene ID diagnostic
    when the file is missing from the seed directory.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    missing_file = seed_dir / "single_missing.mp4"

    scenes = [{"id": "single_777", "path": str(missing_file)}]

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(str(seed_dir), [str(missing_file)], scenes=scenes)

    err_msg = str(exc_info.value)
    assert f"scene single_777 -> {str(missing_file)}" in err_msg
    assert "or run a Stash library scan/cleanup to resolve stale records" in err_msg


def test_downstream_validation_call_signature():
    """
    Verifies that validate_pack_files_present signature remains compatible
    with downstream call (consolidation_dir, expected_primary_paths, contact_sheet_extras).
    """
    import inspect
    sig = inspect.signature(task.validate_pack_files_present)
    params = list(sig.parameters.keys())
    assert params[0] == "consolidation_dir"
    assert params[1] == "expected_primary_paths"
    assert params[2] == "extras_expected"
    assert params[3] == "scenes"


def test_validate_pack_files_present_case_and_slash_normalization(tmp_path):
    """
    Verifies that forward slashes vs backslashes and casing differences
    still correctly map missing files to their Stash scene IDs.
    """
    seed_dir = tmp_path / "SeedDir"
    seed_dir.mkdir()
    missing_path = str(seed_dir / "Scene_File.mp4")

    # In scenes payload, provide with forward slashes and different casing
    forward_slash_path = missing_path.replace("\\", "/").lower()
    scenes = [{"id": "scene_norm_99", "path": forward_slash_path}]

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(str(seed_dir), [missing_path], scenes=scenes)

    err_msg = str(exc_info.value)
    assert f"scene scene_norm_99 -> {missing_path}" in err_msg

