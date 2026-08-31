"""T3 (staged-wizard-inplace-seed): pack-file-presence validation.

Rewrites the former foreign-file-refusal suite (``validate_target_directory_
cleanliness``, deleted in this todo) to the new presence semantics
(``validate_pack_files_present``):

- every declared pack primary file must EXIST under the consolidation dir
  (``seed_dir`` when the payload provides it, ``output_dir/<safe_title>``
  for legacy payloads) at ANY depth — recursive containment, not the old
  direct-parent equality;
- unrelated files, hidden dotfiles and foreign subfolders are IGNORED
  (never scanned, never refused, never touched) — the old refusal
  assertions are inverted;
- a missing primary aborts the build naming each missing path exactly,
  with the hint "Run Consolidate or add the missing files to the seed
  directory", and no torrent is written.

Each rewritten assertion carries a comment mapping old→new behavior.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import torf

PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task

TASK_PY = PLUGIN_DIR / "task.py"
TRACKERS = ["http://tracker.empornium.sx:2710/announce"]


def _run_task_py(payload: dict) -> subprocess.CompletedProcess:
    """Run plugin/task.py as a Stash-native subprocess with a JSON payload on stdin."""
    stdin_payload = {
        "task_name": "BuildMegapack",
        "args": [
            {"key": "mode", "value": "build"},
            {"key": "payload", "value": json.dumps(payload)},
        ],
    }
    return subprocess.run(
        [sys.executable, str(TASK_PY)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# ============================================================================
# Surviving legacy-layout tests (behavior unchanged by the T3 rewrite)
# ============================================================================

def test_megapack_build_succeeds_with_foreign_files_in_output_dir(tmp_path):
    # OLD→NEW: unchanged. Foreign files in the PARENT of the pack dir were never
    # refused (the old scan only looked inside the pack dir) and presence
    # validation does not scan at all — this legacy-layout build still succeeds.
    output_dir = tmp_path / "MyEmmaParent"
    output_dir.mkdir()

    # Foreign files inside output_dir (e.g. 81 foreign files)
    for i in range(10):
        (output_dir / f"unrelated_{i}.mkv").write_bytes(b"FOREIGN_MOVIE")
    (output_dir / "unrelated_notes.txt").write_text("Foreign notes")

    # Dedicated pack subfolder under output_dir
    pack_title = "Emma Pack 2026"
    pack_dir = output_dir / pack_title
    pack_dir.mkdir()

    media_1 = pack_dir / "scene_01.mp4"
    media_2 = pack_dir / "scene_02.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)
    media_2.write_bytes(b"MEDIA_2_DATA" * 5000)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
            {"id": 2, "title": "Scene 2", "path": str(media_2)},
        ],
        "trackers": TRACKERS,
        "include_contact_sheets": False,
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert os.path.exists(result["torrent_path"])

    # Verify torrent payload
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == pack_title
    # Torrent must only include pack files, not foreign files
    t_files = [str(f).replace("\\", "/") for f in t.files]
    assert len(t_files) == 2
    assert "scene_01.mp4" in t_files[0]
    assert "scene_02.mp4" in t_files[1]
    assert bool(t.verify(str(pack_dir))) is True


def test_megapack_build_succeeds_with_unrelated_and_hidden_files_inside_pack_dir(tmp_path):
    # OLD→NEW: replaces test_megapack_build_fails_with_foreign_files_inside_pack_dir,
    # which asserted RuntimeError "Target pack directory.*contains 1 foreign file"
    # for intruder.txt inside the pack dir. The foreign-file refusal is DELETED
    # (todo 3): unrelated files and hidden dotfiles inside the seed/pack dir are
    # now simply ignored — the build must SUCCEED with them present and untouched.
    output_dir = tmp_path / "MyParent"
    output_dir.mkdir()

    pack_title = "Emma Pack 2026"
    pack_dir = output_dir / pack_title
    pack_dir.mkdir()

    media_1 = pack_dir / "scene_01.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)

    # Unrelated + hidden content INSIDE the pack dir (formerly refused)
    intruder = pack_dir / "intruder.txt"
    intruder.write_text("intruder")
    hidden = pack_dir / ".hidden_dotfile"
    hidden.write_text("hidden")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
        ],
        "trackers": TRACKERS,
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert os.path.exists(result["torrent_path"])

    # Unrelated files are never touched by the build
    assert intruder.read_text() == "intruder"
    assert hidden.read_text() == "hidden"


def test_torrent_root_name_equals_pack_title_and_contact_sheets_inside_pack_dir(tmp_path):
    # OLD→NEW: unchanged. Contact sheets ON still copy into the pack dir's
    # "Contact Sheets" subfolder; under presence validation those copies are the
    # extras_expected set and must exist — they do, so this still passes.
    output_dir = tmp_path / "D_Synthetic_Parent"
    output_dir.mkdir()

    pack_title = "Emma Megapack Vol 1"
    pack_dir = output_dir / pack_title
    pack_dir.mkdir()

    media_1 = pack_dir / "scene_01.mkv"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
        ],
        "trackers": TRACKERS,
        "include_contact_sheets": True,
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"

    t = torf.Torrent.read(result["torrent_path"])
    # Release is named after the pack title, NOT parent folder D_Synthetic_Parent
    assert t.name == pack_title

    # Contact sheets must land inside pack_dir / Contact Sheets
    cs_dir = pack_dir / "Contact Sheets"
    assert cs_dir.is_dir()
    assert len(list(cs_dir.glob("*.jpg"))) >= 1

    t_files = [str(f).replace("\\", "/") for f in t.files]
    assert any("Contact Sheets/" in f for f in t_files)
    assert bool(t.verify(str(pack_dir))) is True


def test_compat_rule_when_output_dir_basename_equals_safe_title(tmp_path):
    # OLD→NEW: unchanged. Legacy layout where output_dir IS the pack dir; media
    # directly inside it is trivially "under" it for recursive containment.
    output_dir = tmp_path / "Emma Pack 2026"
    output_dir.mkdir()

    media_1 = output_dir / "scene_01.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)

    payload = {
        "pack_title": "Emma Pack 2026",
        "output_dir": str(output_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
        ],
        "trackers": TRACKERS,
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == "Emma Pack 2026"
    assert bool(t.verify(str(output_dir))) is True


# ============================================================================
# validate_pack_files_present — direct unit tests (new validator)
# ============================================================================

def test_validate_pack_files_present_accepts_files_at_any_depth(tmp_path):
    # OLD→NEW: the old consolidation gate required each file's DIRECT parent to
    # equal the pack dir; presence validation accepts a file at ANY depth under
    # the directory (recursive ancestor check).
    root = tmp_path / "seed"
    nested = root / "Season 01" / "extras"
    nested.mkdir(parents=True)
    media = nested / "scene_01.mp4"
    media.write_bytes(b"MEDIA" * 100)

    task.validate_pack_files_present(str(root), [str(media)])  # must not raise


def test_validate_pack_files_present_is_case_safe_on_windows(tmp_path):
    # OLD→NEW: carries over tier5's case-insensitivity guarantee — declared path
    # casing may differ from on-disk casing; normcase comparison must accept it.
    root = tmp_path / "seed"
    root.mkdir()
    media = root / "SCENE01.MP4"
    media.write_bytes(b"MEDIA" * 100)

    task.validate_pack_files_present(str(root), [str(root / "scene01.mp4")])  # must not raise


def test_validate_pack_files_present_lists_every_missing_path_with_hint(tmp_path):
    # OLD→NEW: the old validator listed FOREIGN files it refused; the new
    # validator lists every MISSING expected primary verbatim, plus the
    # Consolidate hint.
    root = tmp_path / "seed"
    root.mkdir()
    present = root / "scene_01.mp4"
    present.write_bytes(b"MEDIA" * 100)
    missing_flat = root / "scene_02.mp4"  # never created
    missing_nested = root / "sub" / "scene_03.mp4"  # declared nested, absent

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(
            str(root), [str(present), str(missing_flat), str(missing_nested)]
        )
    msg = str(exc_info.value)
    assert str(missing_flat) in msg
    assert str(missing_nested) in msg
    assert "Run Consolidate or add the missing files to the seed directory" in msg


def test_validate_pack_files_present_rejects_existing_file_outside_dir(tmp_path):
    # OLD→NEW: "missing from under the dir" covers a file that exists somewhere
    # else entirely — it must be named as missing, not silently accepted.
    root = tmp_path / "seed"
    root.mkdir()
    elsewhere = tmp_path / "other_library"
    elsewhere.mkdir()
    inside = root / "scene_01.mp4"
    inside.write_bytes(b"MEDIA" * 100)
    outside = elsewhere / "scene_02.mp4"
    outside.write_bytes(b"MEDIA" * 100)

    with pytest.raises(RuntimeError) as exc_info:
        task.validate_pack_files_present(str(root), [str(inside), str(outside)])
    assert str(outside) in str(exc_info.value)


def test_validate_pack_files_present_ignores_unrelated_files(tmp_path):
    # OLD→NEW: the old validator SCANNED the directory and refused any entry not
    # on an allowlist (foreign text files, extra videos, foreign subfolders).
    # The new validator never scans: unrelated files, hidden dotfiles and
    # foreign subfolders have zero effect on the verdict.
    root = tmp_path / "seed"
    root.mkdir()
    media = root / "scene_01.mp4"
    media.write_bytes(b"MEDIA" * 100)
    (root / "unrelated_notes.txt").write_text("notes")
    (root / ".hidden_dotfile").write_text("hidden")
    (root / "extra_video.mp4").write_bytes(b"EXTRA")
    (root / "OtherAlbum").mkdir()

    task.validate_pack_files_present(str(root), [str(media)])  # must not raise


def test_validate_pack_files_present_requires_extras_when_provided(tmp_path):
    # OLD→NEW: the old allowlist PERMITTED a Contact Sheets folder; the new
    # validator REQUIRES its contents (extras_expected) to exist under the dir
    # when include_contact_sheets is ON — they are deliberately written there.
    root = tmp_path / "seed"
    root.mkdir()
    media = root / "scene_01.mp4"
    media.write_bytes(b"MEDIA" * 100)
    cs_dir = root / "Contact Sheets"
    cs_dir.mkdir()
    sheet = cs_dir / "pack_preview_1.jpg"
    sheet.write_bytes(b"\xff\xd8\xff")

    task.validate_pack_files_present(str(root), [str(media)], [str(sheet)])  # must not raise

    with pytest.raises(RuntimeError, match="absent.jpg"):
        task.validate_pack_files_present(
            str(root), [str(media)], [str(cs_dir / "absent.jpg")]
        )


# ============================================================================
# Subprocess: missing pack primary blocks the build (Stash-native protocol)
# ============================================================================

def test_megapack_build_fails_when_pack_primary_missing_from_seed_dir(tmp_path):
    # OLD→NEW: pre-T3, a declared scene path missing from disk was silently
    # DROPPED (existence filter) and the build succeeded with fewer files. Under
    # presence validation the build must exit 1 via DEEPSEEK_TASK_FAILED, name
    # the exact missing filename(s), include the Consolidate hint, and write no
    # torrent anywhere.
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"

    media_1 = seed_dir / "scene_01.mp4"
    media_bytes_1 = b"MEDIA_1_DATA" * 5000
    media_1.write_bytes(media_bytes_1)
    missing_1 = seed_dir / "scene_02.mp4"  # never created
    missing_2 = seed_dir / "scene_03.mp4"  # never created

    run_id = "presence-missing-1"
    payload = {
        "run_id": run_id,
        "pack_title": "Presence Missing Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
            {"id": 2, "title": "Scene 2", "path": str(missing_1)},
            {"id": 3, "title": "Scene 3", "path": str(missing_2)},
        ],
        "trackers": TRACKERS,
        "include_contact_sheets": False,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 1, f"expected failure, got:\n{proc.stderr}"
    assert "DEEPSEEK_TASK_FAILED" in proc.stderr
    # Each missing path is named exactly
    assert "scene_02.mp4" in proc.stderr
    assert "scene_03.mp4" in proc.stderr
    assert "Run Consolidate or add the missing files to the seed directory" in proc.stderr

    # No torrent written anywhere (seed dir or scratch tree)
    assert not list(seed_dir.rglob("*.torrent"))
    assert not list(scratch_dir.rglob("*.torrent"))
    # The present media file is untouched
    assert media_1.read_bytes() == media_bytes_1
