"""
Unit and integration tests for Stage 7 Feature 2: Single-Scene Upload Mode (Backend).
Covers:
- Manifest task registration (BuildSingleScene)
- CLI and JSON payload dispatch
- In-place single-scene build with decoupled payload_source and artifact_dir
- Single-file torrent metainfo structure (info.length, no wrapper folder)
- Media immutability (media file not moved, renamed, or copied)
- Single-scene arity enforcement (0 or >1 media files raises RuntimeError)
- Skipping target cleanliness and consolidation checks in single mode
- Regressions asserting consolidation and cleanliness guards still fire in megapack mode
- Single-scene BBCode formatting (resolution/duration badges, no Scenes Included, no numbered breakdown)
- Preflight checklist verification with external payload_root and info-only root_name
- Forcing include_contact_sheets to False in single mode
"""

import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
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

import task
from empornium_megapack.build import verify_preflight_checklist
from empornium_megapack.config import get_settings


def test_manifest_registers_build_single_scene_task():
    """BuildSingleScene is registered in empornium-megapack.yml with defaultArgs: {mode: single}."""
    manifest_path = PLUGIN_DIR / "empornium-megapack.yml"
    assert manifest_path.exists(), f"Manifest not found at {manifest_path}"

    content = manifest_path.read_text(encoding="utf-8")
    assert "BuildSingleScene" in content
    # Verify task block structure
    pattern = (
        r"-\s+name:\s*BuildSingleScene\s+"
        r"description:\s*[\"'].*?[\"']\s+"
        r"defaultArgs:\s+"
        r"mode:\s*single"
    )
    assert re.search(pattern, content), f"BuildSingleScene task block missing or malformed in {manifest_path}"


def test_cli_and_json_dispatch_single_scene_mode():
    """parse_input_payload and main correctly resolve mode: single across all invocation interfaces."""
    # 1. CLI argument
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["task.py", "single"])
        mode, payload, conn = task.parse_input_payload()
        assert mode == "single"

    # 2. JSON input via task_name
    json_task_name = json.dumps({
        "task_name": "BuildSingleScene",
        "server_connection": {"Scheme": "http", "Port": 9999},
        "args": {}
    })
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["task.py"])
        mp.setattr(sys, "stdin", io.StringIO(json_task_name))
        mode, payload, conn = task.parse_input_payload()
        assert mode == "single"

    # 3. JSON input via list args [{key: "mode", value: {str: "single"}}]
    json_list_args = json.dumps({
        "task_name": "CustomTask",
        "args": [
            {"key": "mode", "value": {"str": "single"}},
            {"key": "payload", "value": json.dumps({"pack_title": "Single Release"})}
        ]
    })
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["task.py"])
        mp.setattr(sys, "stdin", io.StringIO(json_list_args))
        mode, payload, conn = task.parse_input_payload()
        assert mode == "single"
        assert payload.get("pack_title") == "Single Release"

    # 4. JSON input via dict args {"mode": "single"}
    json_dict_args = json.dumps({
        "args": {
            "mode": "single",
            "payload": {"pack_title": "Dict Single"}
        }
    })
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["task.py"])
        mp.setattr(sys, "stdin", io.StringIO(json_dict_args))
        mode, payload, conn = task.parse_input_payload()
        assert mode == "single"
        assert payload.get("pack_title") == "Dict Single"


def test_single_scene_build_in_place_and_torrent_structure(tmp_path):
    """
    Single-scene build builds in-place:
    - payload media file is outside artifact output_dir and is NOT moved, renamed, or copied.
    - .torrent has info.length (single-file torrent, no folder wrapper) and name == media filename.
    - Artifacts land in artifact_dir.
    """
    media_dir = tmp_path / "media_library" / "Studio_X"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "Feature.Scene.2026.1080p.mp4"
    dummy_bytes = b"SINGLE_SCENE_MEDIA_CONTENT_" * 4096
    media_file.write_bytes(dummy_bytes)
    initial_mtime = media_file.stat().st_mtime

    artifact_dir = tmp_path / "artifacts_output"
    artifact_dir.mkdir(parents=True)

    payload = {
        "mode": "single",
        "pack_title": "Feature Scene Release",
        "output_dir": str(artifact_dir),
        "scenes": [
            {
                "id": 42,
                "title": "Feature Scene",
                "path": str(media_file),
                "studio": "Studio X",
                "performers": ["Alice Wonder"],
                "tags": ["1080p", "Feature"],
                "height": 1080,
                "duration": 1800,
            }
        ],
        "notes": "Single scene release notes",
    }

    res = task.run_build_megapack(payload)

    assert res["status"] == "success"
    assert res["task"] == "BuildSingleScene"
    assert res["pack_title"] == "Feature Scene Release"

    # Assert media file was not moved, renamed, or copied
    assert media_file.exists()
    assert media_file.stat().st_size == len(dummy_bytes)
    assert media_file.stat().st_mtime == initial_mtime
    # Assert media file is NOT copied into artifact_dir
    assert not (artifact_dir / media_file.name).exists()

    # Assert artifacts exist in artifact_dir
    torrent_path = Path(res["torrent_path"])
    bbcode_path = Path(res["bbcode_path"])
    manifest_path = Path(res["manifest_path"])
    submission_path = Path(res["submission_path"])

    assert torrent_path.exists()
    assert torrent_path.parent == artifact_dir
    assert bbcode_path.exists()
    assert manifest_path.exists()
    assert submission_path.exists()

    # Inspect the .torrent using torf
    torrent_obj = torf.Torrent.read(str(torrent_path))
    # Critical invariant: must have length in info (single file torrent)
    assert "length" in torrent_obj.metainfo["info"]
    assert "files" not in torrent_obj.metainfo["info"]
    assert torrent_obj.name == "Feature.Scene.2026.1080p.mp4"
    assert torrent_obj.files == [Path("Feature.Scene.2026.1080p.mp4")]
    assert torrent_obj.size == len(dummy_bytes)


def test_single_scene_skips_consolidation_and_cleanliness_guards(tmp_path):
    """Single-scene mode ignores non-consolidated media and dirty files in output_dir."""
    media_dir = tmp_path / "raw_stash_files"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "scene_unconsolidated.mp4"
    media_file.write_bytes(b"VIDEO" * 1024)

    artifact_dir = tmp_path / "dirty_output_dir"
    artifact_dir.mkdir(parents=True)
    # Put unrelated / dirty files in artifact_dir that would fail megapack cleanliness validation
    (artifact_dir / "unrelated_other_movie.mkv").write_bytes(b"FOREIGN_DATA")
    (artifact_dir / "random_document.pdf").write_bytes(b"PDF")

    payload = {
        "single_scene": True,
        "pack_title": "Dirty Dir Test",
        "output_dir": str(artifact_dir),
        "scenes": [
            {
                "id": 1,
                "title": "Unconsolidated Scene",
                "path": str(media_file),
            }
        ],
    }

    # Must succeed without raising consolidation error or cleanliness error
    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    assert os.path.exists(res["torrent_path"])


def test_megapack_consolidation_guard_still_fires_regression(tmp_path):
    """Megapack mode still requires files to be under the pack dir (recursive).

    OLD→NEW (T3): the gate's direct-parent equality check became recursive
    containment and its message changed from "Scenes are not consolidated into
    pack directory" to "Scene files are not under the seed directory"; files at
    any depth under the destination now pass, files outside it still refuse."""
    unconsolidated_dir = tmp_path / "unconsolidated"
    unconsolidated_dir.mkdir(parents=True)
    f1 = unconsolidated_dir / "s1.mp4"
    f2 = unconsolidated_dir / "s2.mp4"
    f1.write_bytes(b"A" * 1024)
    f2.write_bytes(b"B" * 1024)

    dest_dir = tmp_path / "megapack_dest"
    dest_dir.mkdir(parents=True)
    pack_dir = dest_dir / "Unconsolidated Pack"
    pack_dir.mkdir(parents=True)

    payload = {
        "single_scene": False,
        "pack_title": "Unconsolidated Pack",
        "output_dir": str(dest_dir),
        "scenes": [
            {"id": 1, "path": str(f1)},
            {"id": 2, "path": str(f2)},
        ],
    }

    with pytest.raises(RuntimeError, match="Scene files are not under the seed directory"):
        task.run_build_megapack(payload)


def test_megapack_presence_guard_still_fires_regression(tmp_path):
    """Megapack mode still blocks the build when a pack primary is absent.

    OLD→NEW (T3): replaces test_megapack_cleanliness_guard_still_fires_regression,
    which asserted RuntimeError "contains \\d+ foreign file\\(s\\)" for
    foreign_file.iso inside pack_dir. The foreign-file scan is deleted —
    unrelated files no longer block anything; a MISSING primary does."""
    dest_dir = tmp_path / "dirty_megapack_dest"
    dest_dir.mkdir(parents=True)
    pack_dir = dest_dir / "Dirty Megapack"
    pack_dir.mkdir(parents=True)
    f1 = pack_dir / "scene_ok.mp4"
    f1.write_bytes(b"A" * 1024)

    # Unrelated file inside pack_dir: ignored under presence semantics
    (pack_dir / "foreign_file.iso").write_bytes(b"ISO")

    # Declared primary that never landed in pack_dir: blocks the build
    missing = pack_dir / "scene_missing.mp4"

    payload = {
        "single_scene": False,
        "pack_title": "Dirty Megapack",
        "output_dir": str(dest_dir),
        "scenes": [{"id": 1, "path": str(f1)}, {"id": 2, "path": str(missing)}],
    }

    with pytest.raises(RuntimeError, match="missing from"):
        task.run_build_megapack(payload)


def test_single_scene_arity_enforcement(tmp_path):
    """Single-scene mode raises RuntimeError when file count != 1 (naming 0 or >1)."""
    media_dir = tmp_path / "arity_test"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "s1.mp4"
    f2 = media_dir / "s2.mp4"
    f1.write_bytes(b"1" * 1024)
    f2.write_bytes(b"2" * 1024)

    # 1. 0 files
    payload_zero = {
        "single_scene": True,
        "pack_title": "Zero Files",
        "output_dir": str(media_dir),
        "scenes": [],
    }
    with pytest.raises(RuntimeError, match=r"Single-scene mode requires exactly 1 media file, found 0 file\(s\)"):
        task.run_build_megapack(payload_zero)

    # 2. 2 files
    payload_two = {
        "single_scene": True,
        "pack_title": "Two Files",
        "output_dir": str(media_dir),
        "scenes": [
            {"id": 1, "path": str(f1)},
            {"id": 2, "path": str(f2)},
        ],
    }
    with pytest.raises(RuntimeError, match=r"Single-scene mode requires exactly 1 media file, found 2 file\(s\)"):
        task.run_build_megapack(payload_two)


def test_single_scene_bbcode_formatting(tmp_path):
    """Single-scene BBCode contains title with badges, studio, performers, notes, but no Scenes Included or breakdown."""
    media_dir = tmp_path / "bbcode_test"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "solo_star.mp4"
    f1.write_bytes(b"STAR" * 1024)

    payload = {
        "single_scene": True,
        "pack_title": "Solo Star Performance",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 10,
                "title": "Solo Star Performance",
                "path": str(f1),
                "studio": "Star Studios",
                "performers": ["Stella Bright"],
                "tags": ["Solo", "1080p"],
                "height": 1080,
                "duration": 1800,
            }
        ],
        "notes": "Exclusive 1080p single release.",
    }

    res = task.run_build_megapack(payload)
    bbcode = res["bbcode"]

    # Assert single-scene structure. The banner carries the title; the
    # resolution and runtime badges moved into its spec strip.
    assert "[color=#f5f8fa]Solo Star Performance[/color]" in bbcode
    assert "RESOLUTION[/color][/size][br][size=3][color=#f5f8fa][b]1080p[/b]" in bbcode
    assert "RUNTIME[/color][/size][br][size=3][color=#f5f8fa][b]30:00[/b]" in bbcode
    assert "[b][color=#8a9ba8]Studio[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Star Studios[/color]" in bbcode
    assert "[b][color=#8a9ba8]Performers[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]Stella Bright[/color]" in bbcode
    assert "[b][color=#8a9ba8]Tags[/color][/b][color=#5c7080]: [/color][color=#f5f8fa]1080p, Solo[/color]" in bbcode
    assert "[quote]Exclusive 1080p single release.[/quote]" in bbcode

    # Assert Megapack-specific lines are absent
    assert "Scenes Included:" not in bbcode
    assert "1. [b]" not in bbcode
    assert "2. [b]" not in bbcode


def test_single_scene_forces_include_contact_sheets_false(tmp_path):
    """In single mode, include_contact_sheets is forced off and no Contact Sheets subdirectory is generated."""
    media_dir = tmp_path / "cs_test"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "scene_cs.mp4"
    f1.write_bytes(b"CS" * 1024)

    payload = {
        "single_scene": True,
        "include_contact_sheets": True,  # User requested, but should be forced False
        "pack_title": "CS Single Scene",
        "output_dir": str(media_dir),
        "scenes": [{"id": 1, "path": str(f1)}],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    # Verify no "Contact Sheets" subdir was created
    assert not (media_dir / "Contact Sheets").exists()

    # --- omitted-key variant: key absent should also be forced off in single mode ---
    media_dir_2 = tmp_path / "cs_test_omitted"
    media_dir_2.mkdir(parents=True)
    f2 = media_dir_2 / "scene_omitted.mp4"
    f2.write_bytes(b"CS" * 1024)

    payload_omitted = {
        "single_scene": True,
        # include_contact_sheets intentionally omitted
        "pack_title": "CS Single Omitted",
        "output_dir": str(media_dir_2),
        "scenes": [{"id": 1, "path": str(f2)}],
    }

    res2 = task.run_build_megapack(payload_omitted)
    assert res2["status"] == "success"
    assert not (media_dir_2 / "Contact Sheets").exists()
    # Also verify torrent file list has no Contact Sheets entry
    t2 = torf.Torrent.read(res2["torrent_path"])
    cs_in_torrent = [str(f) for f in t2.files if "Contact Sheets" in str(f)]
    assert not cs_in_torrent, f"Contact Sheets found in single-scene torrent (omitted key): {cs_in_torrent}"


def test_preflight_checklist_single_scene_external_payload_root(tmp_path):
    """
    verify_preflight_checklist handles single-file torrent with payload_root pointing to external file:
    - payload_files check resolves against payload_root and passes
    - root_name check is informational (is_warning: False)
    - ready is True when images are remote URLs
    """
    media_dir = tmp_path / "ext_media"
    media_dir.mkdir()
    media_file = media_dir / "single_video.mp4"
    media_file.write_bytes(b"DATA" * 1024 * 512)

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    # Build single-file torrent pointing to media_file
    torrent = torf.Torrent(
        path=str(media_file),
        trackers=[["http://tracker.empornium.sx:2710/passkey/announce"]],
        private=True,
        source="Emp",
    )
    torrent.generate()
    tor_path = artifact_dir / "single_video.torrent"
    torrent.write(str(tor_path))

    sub_data = {
        "title": "Single Video Release Title",
        "tracker_tags": ["tag1", "tag2"],
        "image_urls": ["https://hamsterimg.net/images/2026/08/23/sheet1.jpg"],
        "preview_only": False,
        "torrent_path": str(tor_path),
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=artifact_dir,
        payload_root=media_file,
        pack_title="Single Video Release Title",
        submission_data=sub_data,
    )

    assert res["ready"] is True
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["payload_files"]["passed"] is True
    assert "All 1 payload file(s) exist on disk" in checks_by_id["payload_files"]["detail"]
    assert checks_by_id["root_name"]["passed"] is True
    assert checks_by_id["root_name"]["is_warning"] is False
    assert "Single-file torrent" in checks_by_id["root_name"]["detail"]


def test_preflight_checklist_single_scene_missing_external_file(tmp_path):
    """verify_preflight_checklist fails payload_files check when external file at payload_root is missing."""
    media_dir = tmp_path / "ext_media"
    media_dir.mkdir()
    media_file = media_dir / "single_video.mp4"
    media_file.write_bytes(b"DATA" * 1024 * 512)

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    torrent = torf.Torrent(
        path=str(media_file),
        trackers=[["http://tracker.empornium.sx:2710/passkey/announce"]],
        private=True,
        source="Emp",
    )
    torrent.generate()
    tor_path = artifact_dir / "single_video.torrent"
    torrent.write(str(tor_path))

    # Now simulate deleted/missing media file
    media_file.unlink()

    sub_data = {
        "title": "Single Video Release",
        "tracker_tags": ["tag1"],
        "image_urls": ["https://hamsterimg.net/img.jpg"],
        "preview_only": False,
    }

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=artifact_dir,
        payload_root=media_file,
        pack_title="Single Video Release",
        submission_data=sub_data,
    )

    assert res["ready"] is False
    checks_by_id = {c["id"]: c for c in res["checks"]}
    assert checks_by_id["payload_files"]["passed"] is False
    assert "Missing 1 file(s) on disk" in checks_by_id["payload_files"]["detail"]


def test_single_scene_adversarial_metadata_special_chars_and_unicode(tmp_path):
    """Single-scene mode gracefully escapes BBCode brackets, handles unicode performers, and formats badges."""
    media_dir = tmp_path / "unicode_test"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "unicode_video_2160p.mp4"
    f1.write_bytes(b"UNICODE_MEDIA_DATA" * 1024)

    payload = {
        "single_scene": True,
        "pack_title": "[4K Ultra] Special & Rare: Élodie's Scene [2026] 🎬",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 99,
                "title": "[4K Ultra] Special & Rare: Élodie's Scene [2026] 🎬",
                "path": str(f1),
                "studio": "Art & Lust Productions [Europe]",
                "performers": ["Élodie O'Connor", "Jane \"The Star\" Doe"],
                "tags": ["4k.uhd", "exclusive.release", "star.talent"],
                "height": 2160,
                "duration": 5430,  # 1h 30m 30s -> 1:30:30
            }
        ],
        "notes": "Special release note:\nLine 1: High Quality\nLine 2: 100% [Uncensored]",
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    bbcode = res["bbcode"]

    # Brackets in title are escaped as &#91;/&#93; inside the banner masthead,
    # whose own markup stays raw; the badges are strip cells now.
    assert "[color=#f5f8fa]&#91;4K Ultra&#93; Special & Rare: Élodie's Scene &#91;2026&#93; 🎬[/color]" in bbcode
    assert "RESOLUTION[/color][/size][br][size=3][color=#f5f8fa][b]2160p[/b]" in bbcode
    assert "RUNTIME[/color][/size][br][size=3][color=#f5f8fa][b]1:30:30[/b]" in bbcode
    assert "Art & Lust Productions &#91;Europe&#93;" in bbcode
    assert "Jane \"The Star\" Doe & Élodie O'Connor" in bbcode
    assert "4k.uhd, exclusive.release, star.talent" in bbcode
    assert "Line 2: 100% &#91;Uncensored&#93;" in bbcode


def test_single_scene_missing_and_empty_metadata_fields(tmp_path):
    """Single-scene mode handles completely missing or empty metadata fields without crashing."""
    media_dir = tmp_path / "empty_meta_test"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "minimal_scene.mp4"
    f1.write_bytes(b"MINIMAL_DATA" * 512)

    payload = {
        "single_scene": True,
        "pack_title": "",  # Empty pack_title
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 100,
                "title": "",
                "path": str(f1),
                "studio": "",
                "performers": [],
                "tags": [],
                "height": None,
                "duration": None,
            }
        ],
        "notes": "",
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    bbcode = res["bbcode"]

    # Performers fallback to "Various"
    assert "Various" in bbcode
    assert "[b][color=#8a9ba8]Performers[/color][/b]" in bbcode
    # No studio, tags, or quote blocks
    assert "Studio:" not in bbcode
    assert "Tags:" not in bbcode
    assert "[quote]" not in bbcode


def test_single_scene_nested_file_structures_arity_enforcement(tmp_path):
    """Single-scene mode correctly resolves complex scene file structures and enforces arity."""
    media_dir = tmp_path / "nested_arity"
    media_dir.mkdir(parents=True)
    f1 = media_dir / "part1.mp4"
    f2 = media_dir / "part2.mp4"
    f1.write_bytes(b"PART1" * 1024)
    f2.write_bytes(b"PART2" * 1024)

    # 1. Single scene dict containing multiple files via 'file_paths'
    payload_multi_fps = {
        "single_scene": True,
        "pack_title": "Multi Filepaths Scene",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 1,
                "file_paths": [str(f1), str(f2)],
            }
        ],
    }
    with pytest.raises(RuntimeError, match=r"Single-scene mode requires exactly 1 media file, found 2 file\(s\)"):
        task.run_build_megapack(payload_multi_fps)

    # 2. Single scene dict containing multiple files via 'files' list of dicts
    payload_multi_files = {
        "single_scene": True,
        "pack_title": "Multi Files Scene",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 1,
                "files": [{"path": str(f1)}, {"path": str(f2)}],
            }
        ],
    }
    with pytest.raises(RuntimeError, match=r"Single-scene mode requires exactly 1 media file, found 2 file\(s\)"):
        task.run_build_megapack(payload_multi_files)

    # 3. Single scene pointing to non-existent path -> 0 valid files found
    payload_nonexistent = {
        "single_scene": True,
        "pack_title": "Nonexistent File",
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 1,
                "path": str(media_dir / "does_not_exist.mp4"),
            }
        ],
    }
    with pytest.raises(RuntimeError, match=r"Single-scene mode requires exactly 1 media file, found 0 file\(s\)"):
        task.run_build_megapack(payload_nonexistent)


def test_single_scene_subprocess_main_execution(tmp_path):
    """End-to-end execution of task.py via subprocess with stdin JSON BuildSingleScene task."""
    import subprocess

    media_dir = tmp_path / "proc_media"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "subprocess_test_video.mp4"
    media_file.write_bytes(b"SUBPROCESS_VIDEO_DATA" * 2048)

    artifact_dir = tmp_path / "proc_artifacts"
    artifact_dir.mkdir(parents=True)

    input_payload = {
        "task_name": "BuildSingleScene",
        "server_connection": {"Scheme": "http", "Port": 9999},
        "args": {
            "mode": "single",
            "payload": {
                "pack_title": "Subprocess Single Scene Build",
                "output_dir": str(artifact_dir),
                "scenes": [
                    {
                        "id": 501,
                        "title": "Subprocess Scene Title",
                        "path": str(media_file),
                        "studio": "Subprocess Studios",
                        "performers": ["Actor A", "Actor B"],
                        "tags": ["1080p", "Test"],
                        "height": 1080,
                        "duration": 3600,
                    }
                ],
                "notes": "Subprocess build notes.",
            }
        }
    }

    cmd = [
        sys.executable,
        str(PLUGIN_DIR / "task.py"),
    ]
    proc = subprocess.run(
        cmd,
        input=json.dumps(input_payload),
        text=True,
        capture_output=True,
        cwd=str(PROJECT_ROOT),
    )

    assert proc.returncode == 0, f"Process failed with stderr: {proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["status"] == "success"
    assert result["task"] == "BuildSingleScene"
    assert result["pack_title"] == "Subprocess Single Scene Build"

    # Verify torrent on disk
    tor_path = Path(result["torrent_path"])
    assert tor_path.exists()
    tor_obj = torf.Torrent.read(str(tor_path))
    assert "length" in tor_obj.metainfo["info"]
    assert tor_obj.name == "subprocess_test_video.mp4"
    assert (artifact_dir / "Subprocess Single Scene Build_manifest.json").exists()
    assert (artifact_dir / "Subprocess Single Scene Build_submission.json").exists()
    assert (artifact_dir / "Subprocess Single Scene Build_bbcode.txt").exists()


def test_preflight_checklist_payload_root_path_vs_str_with_missing_and_existing_files(tmp_path):
    """
    Verify preflight checklist behavior with payload_root as Path vs str for both existing and missing files.
    """
    media_dir = tmp_path / "media_root"
    media_dir.mkdir()
    file_a = media_dir / "scene_alpha.mp4"
    file_a.write_bytes(b"ALPHA_PAYLOAD" * 2048)

    tor_a = torf.Torrent(path=str(file_a), trackers=[["http://tracker.empornium.sx:2710/announce"]], private=True, source="Emp")
    tor_a.generate()
    tor_path_a = tmp_path / "scene_alpha.torrent"
    tor_a.write(str(tor_path_a))

    sub_data = {
        "title": "Alpha Release",
        "tracker_tags": ["tag"],
        "image_urls": ["https://hamsterimg.net/alpha.jpg"],
        "preview_only": False,
    }

    # 1. Existing file, payload_root passed as Path
    res_path = verify_preflight_checklist(
        torrent_path=tor_path_a,
        output_dir=tmp_path,
        payload_root=file_a,
        pack_title="Alpha Release",
        submission_data=sub_data,
    )
    assert res_path["ready"] is True
    checks_path = {c["id"]: c for c in res_path["checks"]}
    assert checks_path["payload_files"]["passed"] is True

    # 2. Existing file, payload_root passed as str
    res_str = verify_preflight_checklist(
        torrent_path=str(tor_path_a),
        output_dir=str(tmp_path),
        payload_root=str(file_a),
        pack_title="Alpha Release",
        submission_data=sub_data,
    )
    assert res_str["ready"] is True
    checks_str = {c["id"]: c for c in res_str["checks"]}
    assert checks_str["payload_files"]["passed"] is True

    # 3. Missing file, payload_root passed as Path
    file_missing = media_dir / "scene_missing.mp4"
    tor_m = torf.Torrent(path=str(file_a), trackers=[["http://tracker.empornium.sx:2710/announce"]], private=True, source="Emp")
    tor_m.name = "scene_missing.mp4"
    tor_m.generate()
    tor_path_m = tmp_path / "scene_missing.torrent"
    tor_m.write(str(tor_path_m))

    res_missing_path = verify_preflight_checklist(
        torrent_path=tor_path_m,
        output_dir=tmp_path,
        payload_root=file_missing,
        pack_title="Missing Release",
        submission_data=sub_data,
    )
    assert res_missing_path["ready"] is False
    checks_missing_path = {c["id"]: c for c in res_missing_path["checks"]}
    assert checks_missing_path["payload_files"]["passed"] is False
    assert "Missing 1 file(s) on disk" in checks_missing_path["payload_files"]["detail"]

    # 4. Missing file, payload_root passed as str
    res_missing_str = verify_preflight_checklist(
        torrent_path=str(tor_path_m),
        output_dir=str(tmp_path),
        payload_root=str(file_missing),
        pack_title="Missing Release",
        submission_data=sub_data,
    )
    assert res_missing_str["ready"] is False
    checks_missing_str = {c["id"]: c for c in res_missing_str["checks"]}
    assert checks_missing_str["payload_files"]["passed"] is False
    assert "Missing 1 file(s) on disk" in checks_missing_str["payload_files"]["detail"]


def test_preflight_checklist_windows_paths_and_non_ascii_unicode(tmp_path, monkeypatch):
    """
    Verify handling of Windows path separators (backslashes) and non-ASCII unicode paths
    in single-file payload_root resolution and preflight checklist.
    """
    # Hermetic announce: the torrent must build private with a source tag, which
    # derives from the configured announce. Pin an explicit placeholder instead of
    # leaking a dev-machine local config value into the test outcome.
    monkeypatch.setattr(
        get_settings(),
        "empornium_announce_url",
        "http://tracker.empornium.sx:2710/passkey123/announce",
    )
    unicode_dir = tmp_path / "Média Français [2026]"
    unicode_dir.mkdir()
    media_file = unicode_dir / "Élodie_à_Paris_🎬.mp4"
    media_file.write_bytes(b"UNICODE_VIDEO_DATA" * 2048)

    artifact_dir = tmp_path / "Sortie d'Artefacts"
    artifact_dir.mkdir()

    # Pass Windows backslash path strings
    win_media_path = str(media_file).replace("/", "\\")
    win_artifact_path = str(artifact_dir).replace("/", "\\")

    payload = {
        "mode": "single",
        "pack_title": "Élodie à Paris 🎬 [1080p]",
        "output_dir": win_artifact_path,
        "scenes": [
            {
                "id": 999,
                "title": "Élodie à Paris 🎬",
                "path": win_media_path,
                "studio": "French Art Films",
                "performers": ["Élodie"],
                "tags": ["Paris", "1080p"],
                "height": 1080,
                "duration": 2400,
            }
        ],
        "notes": "Vidéo tournée à Paris.",
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"
    assert res["task"] == "BuildSingleScene"

    tor_path = Path(res["torrent_path"])
    assert tor_path.exists()
    tor_obj = torf.Torrent.read(str(tor_path))
    assert "length" in tor_obj.metainfo["info"]
    assert tor_obj.name == "Élodie_à_Paris_🎬.mp4"

    # Preflight check with Windows path strings and non-ASCII characters
    sub_data = {
        "title": "Élodie à Paris 🎬 [1080p]",
        "tracker_tags": ["paris", "1080p"],
        "image_urls": ["https://hamsterimg.net/elodie.jpg"],
        "preview_only": False,
        "torrent_path": str(tor_path).replace("/", "\\"),
    }

    preflight = verify_preflight_checklist(
        torrent_path=str(tor_path).replace("/", "\\"),
        output_dir=win_artifact_path,
        payload_root=win_media_path,
        pack_title="Élodie à Paris 🎬 [1080p]",
        submission_data=sub_data,
    )

    assert preflight["ready"] is True
    checks = {c["id"]: c for c in preflight["checks"]}
    assert checks["payload_files"]["passed"] is True
    assert checks["root_name"]["passed"] is True
    assert checks["root_name"]["is_warning"] is False


def test_one_scene_megapack_vs_single_scene_torrent_metainfo_differentiation(tmp_path):
    """
    Adversarial verification of the single-file torrent marker:
    - 1-scene Megapack creates a folder torrent (info has 'files', not 'length').
    - 1-scene Single mode creates a single-file torrent (info has 'length', not 'files').
    - preflight checklist root_name handles both correctly according to the marker.
    """
    media_dir = tmp_path / "marker_test"
    media_dir.mkdir()
    pack_title = "One Scene Megapack Release"
    pack_dir = media_dir / pack_title
    pack_dir.mkdir()
    media_file = pack_dir / "solo_scene.mp4"
    media_file.write_bytes(b"MARKER_DIFFERENTIATION_BYTES" * 1024)

    # 1. 1-scene Megapack mode (folder torrent)
    megapack_payload = {
        "single_scene": False,
        "pack_title": pack_title,
        "output_dir": str(media_dir),
        "scenes": [
            {
                "id": 1,
                "title": "Solo Scene",
                "path": str(media_file),
            }
        ],
    }
    res_mega = task.run_build_megapack(megapack_payload)
    tor_mega = torf.Torrent.read(res_mega["torrent_path"])

    # Folder torrent marker assertions
    assert "files" in tor_mega.metainfo["info"]
    assert "length" not in tor_mega.metainfo["info"]
    assert tor_mega.name == pack_title

    # Preflight check on 1-scene megapack recognizes it as a folder torrent
    preflight_mega = verify_preflight_checklist(
        torrent_path=res_mega["torrent_path"],
        output_dir=media_dir,
        pack_title="One Scene Megapack Release",
    )
    checks_mega = {c["id"]: c for c in preflight_mega["checks"]}
    # Since folder name != pack_title, root_name issues a warning for folder torrent
    assert "root_name" in checks_mega
    assert checks_mega["payload_files"]["passed"] is True

    # 2. 1-scene Single mode (single-file torrent)
    single_payload = {
        "single_scene": True,
        "pack_title": "One Scene Single Release",
        "output_dir": str(tmp_path / "single_artifacts"),
        "scenes": [
            {
                "id": 1,
                "title": "Solo Scene",
                "path": str(media_file),
            }
        ],
    }
    res_single = task.run_build_megapack(single_payload)
    tor_single = torf.Torrent.read(res_single["torrent_path"])

    # Single-file torrent marker assertions
    assert "length" in tor_single.metainfo["info"]
    assert "files" not in tor_single.metainfo["info"]
    assert tor_single.name == "solo_scene.mp4"

    # Preflight check on single mode recognizes single-file torrent and produces informational root_name
    preflight_single = verify_preflight_checklist(
        torrent_path=res_single["torrent_path"],
        output_dir=tmp_path / "single_artifacts",
        payload_root=media_file,
        pack_title="One Scene Single Release",
    )
    checks_single = {c["id"]: c for c in preflight_single["checks"]}
    assert checks_single["root_name"]["is_warning"] is False
    assert "Single-file torrent" in checks_single["root_name"]["detail"]


def test_tracker_tag_deduplication_and_case_insensitivity_in_single_mode(tmp_path):
    """
    Adversarial verification of tracker tag deduplication across disparate casing and separators.
    """
    media_dir = tmp_path / "tag_dedup"
    media_dir.mkdir()
    media_file = media_dir / "tag_scene.mp4"
    media_file.write_bytes(b"TAG_DEDUP_DATA" * 512)

    payload = {
        "mode": "single",
        "pack_title": "Tag Dedup Release",
        "output_dir": str(media_dir),
        "tags": ["1080p", "1080P", "Solo.Action", "solo_action", "SOLO-ACTION", "4k.uhd", "4K UHD", "Feature.Film"],
        "scenes": [
            {
                "id": 1,
                "title": "Tag Scene",
                "path": str(media_file),
                "tags": ["1080p", "SOLO.ACTION", "Solo Action", "Feature.Film"],
            }
        ],
    }

    res = task.run_build_megapack(payload)
    assert res["status"] == "success"

    # Check tracker_tags uniqueness
    tags = res["tracker_tags"]
    assert len(tags) == len(set(tags))  # Exact uniqueness
    assert "1080p" in tags
    assert "solo.action" in tags
    assert "4k.uhd" in tags
    assert "feature.film" in tags
    # No duplicate casing variants
    assert "1080P" not in tags
    assert "SOLO.ACTION" not in tags


def test_preflight_checklist_with_none_pack_title_for_single_scene(tmp_path):
    """
    Preflight checklist emits root_name check for single-file torrent even when pack_title is None.
    """
    media_dir = tmp_path / "preflight_none_title"
    media_dir.mkdir()
    media_file = media_dir / "clip.mp4"
    media_file.write_bytes(b"CLIP_DATA" * 512)

    tor = torf.Torrent(path=str(media_file), trackers=[["http://tracker.empornium.sx:2710/announce"]], private=True, source="Emp")
    tor.generate()
    tor_path = media_dir / "clip.torrent"
    tor.write(str(tor_path))

    res = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=media_dir,
        payload_root=media_file,
        pack_title=None,
    )
    checks = {c["id"]: c for c in res["checks"]}
    assert "root_name" in checks
    assert checks["root_name"]["passed"] is True
    assert checks["root_name"]["is_warning"] is False
    assert "Single-file torrent" in checks["root_name"]["detail"]


def test_single_scene_mode_case_insensitivity_and_aliases(tmp_path):
    """
    run_build_megapack handles mode casing variants (Single, SINGLE, single_scene, etc.).
    """
    media_dir = tmp_path / "case_modes"
    media_dir.mkdir()
    media_file = media_dir / "case_test.mp4"
    media_file.write_bytes(b"CASE_TEST" * 512)

    for mode_val in ["Single", "SINGLE", "single_scene", "BuildSingleScene"]:
        payload = {
            "mode": mode_val,
            "pack_title": f"Mode {mode_val} Test",
            "output_dir": str(media_dir),
            "scenes": [{"id": 1, "path": str(media_file)}],
        }
        res = task.run_build_megapack(payload)
        assert res["status"] == "success"
        assert res["task"] == "BuildSingleScene"


def test_verify_preflight_checklist_single_scene_string_payload_root(tmp_path):
    """
    verify_preflight_checklist handles string and Path payload_root pointing to single media file.
    """
    media_dir = tmp_path / "single_preflight"
    media_dir.mkdir()
    media_file = media_dir / "standalone.mp4"
    media_file.write_bytes(b"STANDALONE_DATA" * 512)

    tor = torf.Torrent(path=str(media_file), trackers=[["http://tracker.empornium.sx:2710/announce"]], private=True, source="Emp")
    tor.generate()
    tor_path = media_dir / "standalone.torrent"
    tor.write(str(tor_path))

    sub_data = {
        "title": "Standalone Scene",
        "tracker_tags": ["tag"],
        "image_urls": ["https://hamsterimg.net/standalone.jpg"],
        "preview_only": False,
    }

    # Test with string payload_root
    res_str = verify_preflight_checklist(
        torrent_path=str(tor_path),
        output_dir=str(media_dir),
        payload_root=str(media_file),
        pack_title="Standalone Scene",
        submission_data=sub_data,
    )
    assert res_str["ready"] is True
    checks_str = {c["id"]: c for c in res_str["checks"]}
    assert checks_str["payload_files"]["passed"] is True

    # Test with Path payload_root
    res_path = verify_preflight_checklist(
        torrent_path=tor_path,
        output_dir=media_dir,
        payload_root=media_file,
        pack_title="Standalone Scene",
        submission_data=sub_data,
    )
    assert res_path["ready"] is True
    checks_path = {c["id"]: c for c in res_path["checks"]}
    assert checks_path["payload_files"]["passed"] is True




