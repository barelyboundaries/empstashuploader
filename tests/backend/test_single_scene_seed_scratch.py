"""T5 (staged-wizard-inplace-seed): single-scene seed_dir/scratch_dir parity.

Drives plugin/task.py exactly the way Stash does (JSON on stdin, real
subprocess, task_name ``BuildSingleScene`` + ``mode: single``) to prove the
BuildSingleScene path applies the same seed/scratch model as the megapack
path (T2-T4):

(a) with ``seed_dir`` + ``scratch_dir`` in the payload, every generated
    artifact lands under ``<scratch_dir>/<safe_title>/``, the torrent is
    built over the seed DIR with exact-set inclusion (only the scene's
    file — unrelated/hidden files excluded) and name = seed_dir basename;
(b) a declared scene file missing from the seed dir is refused with the
    exact path + the Consolidate hint (same helper as the megapack path);
(c) a scene file that exists OUTSIDE the seed dir is refused (containment
    parity — legacy single-scene builds over any file, new mode must not);
(d) a legacy payload (``output_dir`` only) keeps today's behavior
    byte-for-byte: single-FILE torrent (info.length, name == media
    filename) and artifacts in output_dir root.
"""

import json
import subprocess
import sys
from pathlib import Path

import torf

PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from empornium_megapack.build import sanitize_name  # noqa: E402

TASK_PY = PLUGIN_DIR / "task.py"

TRACKERS = ["http://tracker.empornium.sx:2710/announce"]


def _run_task_py_single(payload: dict) -> subprocess.CompletedProcess:
    """Run plugin/task.py as a Stash-native subprocess in single-scene mode.

    Mirrors the registered task shape: BuildSingleScene with defaultArgs
    {mode: single}. The mode arg must say "single" — a "build" mode arg would
    override the task_name-derived single mode in parse_input_payload.
    """
    stdin_payload = {
        "task_name": "BuildSingleScene",
        "args": [
            {"key": "mode", "value": "single"},
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
    """Extract the DEEPSEEK_TASK_RESULT sentinel JSON emitted to stderr."""
    marker = f"DEEPSEEK_TASK_RESULT {run_id}: "
    for line in proc.stderr.splitlines():
        if marker in line:
            return json.loads(line.split(marker, 1)[1])
    raise AssertionError(
        f"DEEPSEEK_TASK_RESULT sentinel for run {run_id!r} not found in stderr:\n{proc.stderr}"
    )


def _assert_direct_child(child, parent: Path) -> None:
    """Assert ``child`` resolves to a direct file child of ``parent`` (case-safe on Windows)."""
    child_resolved = Path(child).resolve()
    assert child_resolved.parent == parent.resolve(), (
        f"{child} is not a direct child of {parent}"
    )


# ============================================================================
# (a) seed_dir + scratch_dir: artifacts → scratch, torrent over seed dir
# ============================================================================

def test_single_scene_seed_scratch_relocates_artifacts_and_torrent_over_seed(tmp_path):
    """Given a single-scene payload with seed_dir + scratch_dir, When the build
    runs, Then every artifact lands under <scratch_dir>/<safe_title>/, the
    torrent is built over the seed dir with exact-set inclusion (only the
    scene's file) and name = seed_dir basename."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"

    pack_title = "Single Seed Scene"
    safe_title = sanitize_name(pack_title)

    media = seed_dir / "feature_scene.mp4"
    media_bytes = b"SINGLE_SCENE_MEDIA_CONTENT_" * 4096
    media.write_bytes(media_bytes)

    # Unrelated + hidden content in the seed dir must NOT enter the torrent
    (seed_dir / "unrelated_notes.txt").write_text("user notes")
    (seed_dir / ".hidden_dotfile").write_text("hidden")

    run_id = "single-seed-scratch-1"
    payload = {
        "run_id": run_id,
        "pack_title": pack_title,
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "mode": "single",
        "scenes": [
            {
                "id": 42,
                "title": "Feature Scene",
                "path": str(media),
                "height": 1080,
                "duration": 1800,
            }
        ],
        "trackers": TRACKERS,
    }

    proc = _run_task_py_single(payload)
    assert proc.returncode == 0, f"build failed:\n{proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["status"] == "success"
    assert result["task"] == "BuildSingleScene"

    pack_scratch = scratch_dir / safe_title
    assert pack_scratch.is_dir(), f"per-pack scratch folder missing: {pack_scratch}"

    # Every artifact path reported by the result lives in the per-pack scratch folder
    for key in ("torrent_path", "bbcode_path", "manifest_path", "submission_path"):
        _assert_direct_child(result[key], pack_scratch)

    # Physical layout: the expected artifacts under scratch/<safe_title>/
    names = {p.name for p in pack_scratch.iterdir()}
    assert f"{safe_title}.torrent" in names
    assert f"{safe_title}_bbcode.txt" in names
    assert f"{safe_title}_submission.json" in names
    assert f"{safe_title}_manifest.json" in names
    assert "thumbs" in names and (pack_scratch / "thumbs").is_dir()
    assert any(n.startswith(f"{safe_title}_preview") for n in names)

    # The seed dir received NOTHING and its media is byte-identical
    assert sorted(p.name for p in seed_dir.iterdir()) == [
        ".hidden_dotfile",
        "feature_scene.mp4",
        "unrelated_notes.txt",
    ]
    assert media.read_bytes() == media_bytes

    # Torrent over the seed DIR: name = seed_dir basename, file list is
    # EXACTLY the scene's file — no unrelated_notes.txt, no .hidden_dotfile.
    t = torf.Torrent.read(result["torrent_path"])
    assert t.name == seed_dir.name
    torrent_relpaths = {"/".join(f.parts[1:]) for f in t.files}
    assert torrent_relpaths == {"feature_scene.mp4"}
    assert bool(t.verify(str(seed_dir))) is True

    # DEEPSEEK_TASK_RESULT sentinel carries the scratch locations
    sentinel = _result_sentinel(proc, run_id)
    for key in ("torrent_path", "manifest_path", "submission_path"):
        _assert_direct_child(sentinel[key], pack_scratch)


# ============================================================================
# (b) declared scene file missing from the seed dir → refusal with exact path
# ============================================================================

def test_single_scene_seed_missing_file_fails_with_exact_path_and_hint(tmp_path):
    """Given a single-scene payload whose declared file does not exist under
    the provided seed_dir, When the build runs, Then it exits 1 naming the
    exact missing path with the Consolidate hint (megapack parity)."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"

    missing_path = seed_dir / "missing_scene.mp4"
    payload = {
        "run_id": "single-seed-missing-1",
        "pack_title": "Single Missing Scene",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "mode": "single",
        "scenes": [{"id": 7, "title": "Missing Scene", "path": str(missing_path)}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py_single(payload)
    assert proc.returncode == 1
    assert "DEEPSEEK_TASK_FAILED" in proc.stderr
    # The exact missing path + the same hint the megapack path emits
    assert str(missing_path) in proc.stderr
    assert "Run Consolidate or add the missing files to the seed directory" in proc.stderr
    # Nothing was built
    assert not (scratch_dir / sanitize_name("Single Missing Scene") / "Single_Missing_Scene.torrent").exists()


# ============================================================================
# (c) scene file outside the seed dir → containment refusal (new mode only)
# ============================================================================

def test_single_scene_file_outside_seed_dir_is_refused(tmp_path):
    """Given a single-scene payload whose file exists OUTSIDE the provided
    seed_dir, When the build runs, Then it exits 1 (containment parity with
    the megapack path) instead of building a torrent over the stray file."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    scratch_dir = tmp_path / "scratch"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    media = elsewhere / "stray_scene.mp4"
    media.write_bytes(b"STRAY_SCENE_MEDIA" * 2048)

    payload = {
        "run_id": "single-seed-outside-1",
        "pack_title": "Single Stray Scene",
        "seed_dir": str(seed_dir),
        "scratch_dir": str(scratch_dir),
        "mode": "single",
        "scenes": [{"id": 9, "title": "Stray Scene", "path": str(media)}],
        "trackers": TRACKERS,
    }

    proc = _run_task_py_single(payload)
    assert proc.returncode == 1
    assert "DEEPSEEK_TASK_FAILED" in proc.stderr
    assert str(media) in proc.stderr
    assert "Run Consolidate or add the missing files to the seed directory" in proc.stderr


# ============================================================================
# (d) legacy payload (output_dir only): today's single-file torrent, unchanged
# ============================================================================

def test_single_scene_legacy_payload_keeps_single_file_torrent(tmp_path):
    """Given a single-scene payload WITHOUT seed_dir/scratch_dir, When the
    build runs, Then artifacts land in output_dir root and the torrent is a
    single-FILE torrent (info.length, name == media filename) — today's
    behavior byte-for-byte."""
    media_dir = tmp_path / "media_library" / "Studio_X"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "Legacy.Scene.2026.1080p.mp4"
    dummy_bytes = b"LEGACY_SINGLE_SCENE_MEDIA_" * 4096
    media_file.write_bytes(dummy_bytes)

    output_dir = tmp_path / "LegacyOut"
    output_dir.mkdir()

    payload = {
        "run_id": "single-legacy-1",
        "pack_title": "Legacy Single Scene",
        "output_dir": str(output_dir),
        "mode": "single",
        "scenes": [
            {
                "id": 42,
                "title": "Legacy Scene",
                "path": str(media_file),
                "height": 1080,
                "duration": 1800,
            }
        ],
        "trackers": TRACKERS,
    }

    proc = _run_task_py_single(payload)
    assert proc.returncode == 0, f"build failed:\n{proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["status"] == "success"
    assert result["task"] == "BuildSingleScene"

    safe_title = sanitize_name("Legacy Single Scene")

    # Artifacts in output_dir root (today's layout), NOT in a scratch tree
    _assert_direct_child(result["torrent_path"], output_dir)
    assert (output_dir / f"{safe_title}.torrent").exists()
    assert (output_dir / f"{safe_title}_manifest.json").exists()
    assert (output_dir / f"{safe_title}_bbcode.txt").exists()
    assert (output_dir / f"{safe_title}_submission.json").exists()

    # Single-FILE torrent: info.length present, no files list, name = media filename
    t = torf.Torrent.read(result["torrent_path"])
    assert "length" in t.metainfo["info"]
    assert "files" not in t.metainfo["info"]
    assert t.name == "Legacy.Scene.2026.1080p.mp4"
    assert t.size == len(dummy_bytes)

    # Media untouched
    assert media_file.exists()
    assert media_file.stat().st_size == len(dummy_bytes)
