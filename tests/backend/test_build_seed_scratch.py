"""T2 (staged-wizard-inplace-seed): seed_dir/scratch_dir payload inputs.

Drives plugin/task.py exactly the way Stash does (JSON on stdin, real
subprocess) to prove:

(a) with ``seed_dir`` + ``scratch_dir`` in the payload, EVERY generated
    artifact lands under ``<scratch_dir>/<safe_title>/`` and the seed dir
    receives nothing (media checksums unchanged, no new files);
(b) a legacy payload (``output_dir`` only) still produces today's layout;
(c) nesting violations (scratch inside seed, seed inside scratch) exit 1 with
    an error naming BOTH paths;
(d) an explicitly provided ``seed_dir`` that does not exist is refused;
(e) an empty ``seed_dir`` string is refused;
(f) pasted covers relocate into ``<scratch_dir>/<safe_title>/covers/``.
"""

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import torf

PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task  # noqa: E402  (imports the backend package via task's resolver)
from empornium_megapack.build import sanitize_name  # noqa: E402

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


def _result_sentinel(proc: subprocess.CompletedProcess, run_id: str) -> dict:
    """Extract the EMPORNIUM_TASK_RESULT sentinel JSON emitted to stderr."""
    marker = f"EMPORNIUM_TASK_RESULT {run_id}: "
    for line in proc.stderr.splitlines():
        if marker in line:
            return json.loads(line.split(marker, 1)[1])
    raise AssertionError(
        f"EMPORNIUM_TASK_RESULT sentinel for run {run_id!r} not found in stderr:\n{proc.stderr}"
    )


def _assert_direct_child(child, parent: Path) -> None:
    """Assert ``child`` resolves to a direct file child of ``parent`` (case-safe on Windows)."""
    child_resolved = Path(child).resolve()
    assert child_resolved.parent == parent.resolve(), (
        f"{child} is not a direct child of {parent}"
    )


# ============================================================================
# (a) seed_dir + scratch_dir payload: full artifact relocation to scratch
# ============================================================================

def test_seed_scratch_payload_relocates_all_artifacts_to_scratch(tmp_path):
    """Given seed/scratch payload keys, When the build runs, Then every artifact
    lands under <scratch_dir>/<safe_title>/ and the seed dir receives nothing."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"

    pack_title = "Seed Scratch Pack"
    safe_title = sanitize_name(pack_title)

    media_1 = seed_dir / "scene_01.mp4"
    media_2 = seed_dir / "scene_02.mp4"
    media_bytes_1 = b"MEDIA_1_DATA" * 5000
    media_bytes_2 = b"MEDIA_2_DATA" * 5000
    media_1.write_bytes(media_bytes_1)
    media_2.write_bytes(media_bytes_2)

    run_id = "seed-scratch-relocate-1"
    payload = {
        "run_id": run_id,
        "pack_title": pack_title,
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
            {"id": 2, "title": "Scene 2", "path": str(media_2)},
        ],
        "trackers": TRACKERS,
        "include_contact_sheets": False,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 0, f"build failed:\n{proc.stderr}"

    result = json.loads(proc.stdout)
    assert result["status"] == "success"

    pack_scratch = scratch_dir / safe_title
    assert pack_scratch.is_dir(), f"per-pack scratch folder missing: {pack_scratch}"

    # Every artifact path reported by the result lives in the per-pack scratch folder
    for key in ("torrent_path", "bbcode_path", "manifest_path", "submission_path"):
        _assert_direct_child(result[key], pack_scratch)
    for cs in result["contact_sheets"]:
        _assert_direct_child(cs, pack_scratch)

    # Physical layout: exactly the expected artifacts under scratch/<safe_title>/
    names = {p.name for p in pack_scratch.iterdir()}
    assert f"{safe_title}.torrent" in names
    assert f"{safe_title}_bbcode.txt" in names
    assert f"{safe_title}_submission.json" in names
    assert f"{safe_title}_manifest.json" in names
    assert "thumbs" in names and (pack_scratch / "thumbs").is_dir()
    assert any(n.startswith(f"{safe_title}_preview") for n in names)

    # The seed dir received NOTHING and its media is byte-identical
    assert sorted(p.name for p in seed_dir.iterdir()) == ["scene_01.mp4", "scene_02.mp4"]
    assert media_1.read_bytes() == media_bytes_1
    assert media_2.read_bytes() == media_bytes_2

    # T4: torrent name is the seed dir's BASENAME (torf default; the
    # name=safe_title override is gone), and the file list is EXACTLY the pack
    # set — no unrelated_notes.txt, no .hidden_dotfile, no UnrelatedAlbum copy.
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == seed_dir.name
    torrent_relpaths = {"/".join(f.parts[1:]) for f in t.files}
    assert torrent_relpaths == {"scene_01.mp4", "scene_02.mp4"}
    assert bool(t.verify(str(seed_dir))) is True

    # EMPORNIUM_TASK_RESULT sentinel carries the scratch locations
    sentinel = _result_sentinel(proc, run_id)
    for key in ("torrent_path", "manifest_path", "submission_path"):
        _assert_direct_child(sentinel[key], pack_scratch)


# ============================================================================
# (b) legacy payload (output_dir only): today's layout, byte-for-byte
# ============================================================================

def test_legacy_payload_output_dir_only_keeps_today_layout(tmp_path):
    """Given a payload without seed_dir/scratch_dir, When the build runs, Then
    artifacts land in output_dir root and media in output_dir/<safe_title>/."""
    output_dir = tmp_path / "LegacyOut"
    output_dir.mkdir()

    pack_title = "Legacy Layout Pack"
    safe_title = sanitize_name(pack_title)
    pack_dir = output_dir / safe_title
    pack_dir.mkdir()
    media_1 = pack_dir / "scene_01.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)

    payload = {
        "run_id": "legacy-layout-1",
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [{"id": 1, "title": "Scene 1", "path": str(media_1)}],
        "trackers": TRACKERS,
        "include_contact_sheets": False,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 0, f"build failed:\n{proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["status"] == "success"

    # Artifacts in output_dir root (today's layout), NOT in a scratch tree
    _assert_direct_child(result["torrent_path"], output_dir)
    assert (output_dir / f"{safe_title}.torrent").exists()
    assert (output_dir / f"{safe_title}_manifest.json").exists()
    assert (output_dir / f"{safe_title}_bbcode.txt").exists()
    assert (output_dir / f"{safe_title}_submission.json").exists()
    assert (output_dir / "thumbs").is_dir()
    # Media still consolidated in output_dir/<safe_title>
    assert (pack_dir / "scene_01.mp4").exists()

    # T4: the name= override removal preserves legacy naming — the torrent is
    # named after the pack folder's basename, which IS safe_title in legacy mode.
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == safe_title
    assert bool(t.verify(str(pack_dir))) is True


# ============================================================================
# (c) nesting violations: neither path may contain the other
# ============================================================================

def test_scratch_inside_seed_is_rejected_naming_both_paths(tmp_path):
    """Given scratch_dir inside seed_dir, When the build runs, Then it exits 1
    with an error naming both paths and writes no torrent."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    media_1 = seed_dir / "scene_01.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)
    scratch_dir = seed_dir / "scratch"

    payload = {
        "run_id": "nest-scratch-in-seed-1",
        "pack_title": "Nest Reject Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [{"id": 1, "title": "Scene 1", "path": str(media_1)}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 1
    assert "EMPORNIUM_TASK_FAILED" in proc.stderr
    # The error must name BOTH offending paths
    assert str(seed_dir) in proc.stderr
    assert str(scratch_dir) in proc.stderr
    # Nothing was built and the seed dir is untouched
    assert sorted(p.name for p in seed_dir.iterdir()) == ["scene_01.mp4"]
    assert not list(scratch_dir.glob("**/*.torrent")) if scratch_dir.exists() else True


def test_seed_inside_scratch_is_rejected_naming_both_paths(tmp_path):
    """Given seed_dir inside scratch_dir, When the build runs, Then it exits 1
    with an error naming both paths."""
    scratch_dir = tmp_path / "scratch"
    seed_dir = scratch_dir / "seed"
    seed_dir.mkdir(parents=True)
    media_1 = seed_dir / "scene_01.mp4"
    media_1.write_bytes(b"MEDIA_1_DATA" * 5000)

    payload = {
        "run_id": "nest-seed-in-scratch-1",
        "pack_title": "Nest Reject Pack 2",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [{"id": 1, "title": "Scene 1", "path": str(media_1)}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 1
    assert "EMPORNIUM_TASK_FAILED" in proc.stderr
    assert str(seed_dir) in proc.stderr
    assert str(scratch_dir) in proc.stderr


# ============================================================================
# (d) explicitly provided seed_dir must exist
# ============================================================================

def test_missing_explicit_seed_dir_is_rejected(tmp_path):
    """Given a seed_dir that does not exist, When the build runs, Then it exits 1
    naming the missing directory."""
    seed_dir = tmp_path / "does_not_exist"
    scratch_dir = tmp_path / "scratch"

    payload = {
        "run_id": "missing-seed-1",
        "pack_title": "Missing Seed Pack",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [{"id": 1, "title": "Scene 1", "path": r"C:\fake\missing.mp4"}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 1
    assert "EMPORNIUM_TASK_FAILED" in proc.stderr
    assert str(seed_dir) in proc.stderr
    assert "does not exist" in proc.stderr


# ============================================================================
# (e) provided seed_dir/scratch_dir must be non-empty
# ============================================================================

def test_empty_seed_dir_string_is_rejected(tmp_path):
    """Given seed_dir present but blank, When the build runs, Then it exits 1
    demanding a non-empty path instead of silently falling back."""
    payload = {
        "run_id": "empty-seed-1",
        "pack_title": "Empty Seed Pack",
        "seed_dir": "   ",
        "scratch_dir": str(tmp_path / "scratch"),
        "scenes": [{"id": 1, "title": "Scene 1", "path": r"C:\fake\missing.mp4"}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 1
    assert "EMPORNIUM_TASK_FAILED" in proc.stderr
    assert "seed_dir" in proc.stderr
    assert "non-empty" in proc.stderr


# ============================================================================
# (g) T3: seed dir with pack files + unrelated files + hidden files builds
# ============================================================================

def test_seed_dir_with_unrelated_and_hidden_files_builds_successfully(tmp_path):
    """Given a seed dir containing pack media + unrelated files + hidden
    dotfiles + an unrelated subfolder, When the build runs, Then it succeeds
    (T3 deleted the foreign-file refusal, which scanned consolidation_dir ==
    seed_dir in new mode and would have refused these entries): unrelated
    content is ignored — never scanned, never refused, never touched."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"

    pack_title = "Mixed Dir Pack"
    safe_title = sanitize_name(pack_title)

    media_1 = seed_dir / "scene_01.mp4"
    media_2 = seed_dir / "scene_02.mp4"
    media_bytes_1 = b"MEDIA_1_DATA" * 5000
    media_bytes_2 = b"MEDIA_2_DATA" * 5000
    media_1.write_bytes(media_bytes_1)
    media_2.write_bytes(media_bytes_2)

    # Unrelated + hidden content in the seed dir (refused by the pre-T3
    # foreign-file scan; ignored from T3 on)
    (seed_dir / "unrelated_notes.txt").write_text("user notes")
    (seed_dir / ".hidden_dotfile").write_text("hidden")
    unrelated_sub = seed_dir / "UnrelatedAlbum"
    unrelated_sub.mkdir()
    (unrelated_sub / "other_movie.mkv").write_bytes(b"UNRELATED_MOVIE")

    run_id = "mixed-dir-build-1"
    payload = {
        "run_id": run_id,
        "pack_title": pack_title,
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "scenes": [
            {"id": 1, "title": "Scene 1", "path": str(media_1)},
            {"id": 2, "title": "Scene 2", "path": str(media_2)},
        ],
        "trackers": TRACKERS,
        "include_contact_sheets": False,
    }

    proc = _run_task_py(payload)
    assert proc.returncode == 0, f"build failed:\n{proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["status"] == "success"
    assert Path(result["torrent_path"]).exists()

    # Unrelated files are untouched (presence + content)
    assert (seed_dir / "unrelated_notes.txt").read_text() == "user notes"
    assert (seed_dir / ".hidden_dotfile").read_text() == "hidden"
    assert (unrelated_sub / "other_movie.mkv").read_bytes() == b"UNRELATED_MOVIE"
    # Pack media byte-identical
    assert media_1.read_bytes() == media_bytes_1
    assert media_2.read_bytes() == media_bytes_2

    # Artifacts still land only under the per-pack scratch folder
    pack_scratch = scratch_dir / safe_title
    assert pack_scratch.is_dir()
    assert (pack_scratch / f"{safe_title}.torrent").exists()

    # T4: the torrent contains EXACTLY the pack set — the unrelated files
    # (root + subdir) and the hidden dotfile are excluded by exact relative
    # path, and the torrent is named after the seed dir's basename.
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == seed_dir.name
    torrent_relpaths = {"/".join(f.parts[1:]) for f in t.files}
    assert torrent_relpaths == {"scene_01.mp4", "scene_02.mp4"}
    assert torrent_relpaths == {"scene_01.mp4", "scene_02.mp4"}
    assert bool(t.verify(str(seed_dir))) is True


# ============================================================================
# (f) pasted covers relocate into the per-pack scratch folder
# ============================================================================

def test_upload_cover_relocates_pasted_cover_into_pack_scratch(tmp_path, monkeypatch):
    """Given scratch_dir in the UploadCoverImage payload, When the cover is
    processed, Then the local copy lands in <scratch_dir>/<safe_title>/covers/."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    # HamsterImg is an external service: mock the narrowest seam (the upload call).
    monkeypatch.setattr(
        task._domain_images, "upload_hamster", lambda path, settings=None: "http://example.test/cover.jpg"
    )

    scratch_dir = tmp_path / "scratch"
    pack_title = "Cover Scratch Pack"
    payload = {
        "run_id": "cover_run_1",
        "pack_title": pack_title,
        "scratch_dir": str(scratch_dir),
        "image_b64": img_b64,
    }

    result = task.run_upload_cover(payload)

    covers_dir = scratch_dir / sanitize_name(pack_title) / "covers"
    _assert_direct_child(result["local_path"], covers_dir)
    assert (covers_dir / "cover_run_1.jpg").exists()
