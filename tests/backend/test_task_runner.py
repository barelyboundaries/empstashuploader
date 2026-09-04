"""
Unit tests for the native Stash task runner (plugin/task.py).
"""

import sys
import os
import io
import json
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

# Add plugin dir to sys.path
PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task


# --- test media fixture -------------------------------------------------------
# run_build_megapack now refuses to build a pack with no valid media (it would
# emit a torrent with zero piece hashes). Tests below exercise lockfiles, unicode
# and payload handling -- not the empty-input path -- so they need one real file.
# Content is irrelevant: torf hashes bytes, and vcsi failing on a non-video falls
# back to the Pillow placeholder with a warning.
_DUMMY_MEDIA_DIR = None


def _dummy_media_path(target_dir=None, pack_title=None):
    if target_dir is not None:
        if pack_title:
            target_dir = os.path.join(str(target_dir), task.sanitize_name(pack_title))
        os.makedirs(str(target_dir), exist_ok=True)
        p = os.path.join(str(target_dir), "dummy_media.mp4")
    else:
        global _DUMMY_MEDIA_DIR
        if _DUMMY_MEDIA_DIR is None:
            _DUMMY_MEDIA_DIR = tempfile.mkdtemp(prefix="megapack_test_media_")
        p = os.path.join(_DUMMY_MEDIA_DIR, "dummy_media.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 65536)
    return p
# ------------------------------------------------------------------------------


def test_check_dependencies():
    # Verify check_dependencies runs without crashing in test environment
    os.environ["TESTING"] = "1"
    task.check_dependencies()


def test_check_dependencies_missing_package():
    stderr_capture = io.StringIO()
    sys.stderr = stderr_capture
    os.environ["TESTING"] = "1"
    
    orig_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("Mock missing PIL")
        return orig_import(name, *args, **kwargs)

    try:
        with patch("builtins.__import__", side_effect=mock_import):
            task.check_dependencies()
        output = stderr_capture.getvalue()
        assert "\x01e\x02Missing packages:" in output
        assert "pillow" in output
    finally:
        sys.stderr = sys.__stderr__


def test_check_dependencies_missing_binary():
    stderr_capture = io.StringIO()
    sys.stderr = stderr_capture
    os.environ["TESTING"] = "1"

    try:
        with patch("shutil.which", return_value=None):
            task.check_dependencies()
        output = stderr_capture.getvalue()
        assert "\x01w\x02Missing binaries:" in output
        assert "vcsi" in output
        assert "ffmpeg" in output
    finally:
        sys.stderr = sys.__stderr__


# --- bootstrap gate (ensure_python_env + import-time check_dependencies) -------


def _task_source():
    return (PLUGIN_DIR / "task.py").read_text(encoding="utf-8")


def _module_level_call_lines(source):
    """Module-level zero-arg call sites and the first heavy-import line, in order."""
    import ast

    tree = ast.parse(source)
    calls = {}
    first_heavy_import = None
    for node in tree.body:
        if first_heavy_import is None and isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            roots = {n.split(".")[0] for n in names}
            if getattr(node, "module", None):
                roots.add(node.module.split(".")[0])
            if roots & {"torf", "empornium_megapack"}:
                first_heavy_import = node.lineno
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Name) and fn.id not in calls:
                calls[fn.id] = node.lineno
    return calls, first_heavy_import


def test_bootstrap_order_env_gate_before_check_deps_before_heavy_imports():
    calls, first_heavy_import = _module_level_call_lines(_task_source())
    assert "ensure_python_env" in calls, "env gate must run at import time"
    assert "check_dependencies" in calls, "dependency check must run at import time"
    assert calls["ensure_python_env"] < calls["check_dependencies"]
    assert calls["check_dependencies"] < first_heavy_import


def test_check_dependencies_python_312_required_when_tomllib_missing(monkeypatch, capsys):
    os.environ["TESTING"] = "1"
    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "tomllib":
            raise ImportError("No module named 'tomllib'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    with pytest.raises(SystemExit) as excinfo:
        task.check_dependencies()
    assert excinfo.value.code == 1
    assert "Python 3.12+ required" in capsys.readouterr().err


def test_check_dependencies_missing_package_mentions_requirements_txt(monkeypatch, capsys):
    os.environ["TESTING"] = "1"
    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("Mock missing PIL")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    task.check_dependencies()
    output = capsys.readouterr().err
    assert "\x01e\x02Missing packages: pillow" in output
    assert "Install with: pip install -r requirements.txt (see plugin README)" in output


def test_ensure_python_env_reexecs_into_venv_with_absolute_script_path(monkeypatch, tmp_path):
    fake_venv = tmp_path / "venv"
    (fake_venv / "Scripts").mkdir(parents=True)
    fake_exe = fake_venv / "Scripts" / "python.exe"
    fake_exe.write_text("", encoding="utf-8")

    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name in ("empornium_megapack", "torf"):
            raise ImportError(name)
        return orig_import(name, *args, **kwargs)

    captured = []

    def fake_execv(path, args):
        captured.append((path, list(args)))
        raise SystemExit(99)  # real execv never returns

    monkeypatch.setattr("builtins.__import__", mock_import)
    monkeypatch.setattr(os, "execv", fake_execv)
    monkeypatch.setenv("EMPORNIUM_VENV", str(fake_venv))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("EMPORNIUM_REEXEC_VISITED", raising=False)

    try:
        with pytest.raises(SystemExit):
            task.ensure_python_env()
    finally:
        marker = os.environ.pop("EMPORNIUM_REEXEC_VISITED", None)

    assert len(captured) == 1
    path, args = captured[0]
    assert path == str(fake_exe)
    assert args[0] == path
    assert args[1] == str((PLUGIN_DIR / "task.py").resolve())
    assert Path(args[1]).is_absolute()
    assert args[2:] == sys.argv[1:]
    assert marker and str(fake_exe.resolve()) in marker


def test_ensure_python_env_noop_when_dependencies_importable(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("execv must not run when dependencies import cleanly")

    monkeypatch.setattr(os, "execv", boom)
    assert task.ensure_python_env() is None


def test_ensure_python_env_falls_through_when_no_venv_candidate(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("execv must not run when no candidate venv exists")

    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name in ("empornium_megapack", "torf"):
            raise ImportError(name)
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(os, "execv", boom)
    monkeypatch.setattr("builtins.__import__", mock_import)
    monkeypatch.setattr(task, "CURRENT_DIR", tmp_path / "plugin")
    monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path / "cwd")
    monkeypatch.delenv("EMPORNIUM_VENV", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    assert task.ensure_python_env() is None


def test_emit_progress():
    stderr_capture = io.StringIO()
    sys.stderr = stderr_capture
    try:
        task.emit_progress(0.4567, "Testing progress step")
        output = stderr_capture.getvalue()
        assert "\x01p\x020.4567\n" in output
        assert "[45%] Testing progress step" in output
    finally:
        sys.stderr = sys.__stderr__


def test_emit_progress_clamping():
    stderr_capture = io.StringIO()
    sys.stderr = stderr_capture
    try:
        task.emit_progress(1.5)
        assert "\x01p\x021.0000\n" in stderr_capture.getvalue()
        
        stderr_capture.seek(0)
        stderr_capture.truncate(0)
        
        task.emit_progress(-0.5)
        assert "\x01p\x020.0000\n" in stderr_capture.getvalue()

        stderr_capture.seek(0)
        stderr_capture.truncate(0)

        task.emit_progress(float("nan"))
        assert "\x01p\x020.0000\n" in stderr_capture.getvalue()
    finally:
        sys.stderr = sys.__stderr__


def test_sanitize_name():
    assert task.sanitize_name('test/pack:name*with?bad"chars<and>pipes') == "test_pack_name_with_bad_chars_and_pipes"
    assert task.sanitize_name("CON") == "_CON"
    assert task.sanitize_name("NUL") == "_NUL"
    assert task.sanitize_name("   hello world   ") == "hello world"
    assert task.sanitize_name("") == "Untitled"


def test_get_win32_creation_time(tmp_path):
    f = tmp_path / "test_file.txt"
    f.write_text("hello")
    ctime = task.get_win32_creation_time(str(f))
    assert isinstance(ctime, float)
    assert ctime > 0


def test_get_volume_serial_and_can_hardlink(tmp_path):
    f1 = tmp_path / "file1.txt"
    f2 = tmp_path / "file2.txt"
    f1.write_text("a")
    f2.write_text("b")

    can_link = task.can_hardlink(str(f1), str(f2))
    assert can_link is True


def test_run_probe_files(tmp_path):
    # Create sample files
    f1 = tmp_path / "scene_1.mp4"
    f1.write_text("dummy video content 1")
    f2 = tmp_path / "scene_2.mp4"
    f2.write_text("dummy video content 2")
    f3 = tmp_path / "scene_1.mp4"  # Duplicate basename test

    payload = {
        "target_dir": str(tmp_path),
        "files": [
            {"scene_id": 101, "path": str(f1)},
            {"scene_id": 102, "path": str(f2)},
            {"scene_id": 103, "path": str(f1)},  # Duplicate reference
            {"scene_id": 104, "path": str(tmp_path / "non_existent.mp4")}
        ]
    }

    result = task.run_probe_files(payload)

    assert result["status"] == "success"
    assert result["task"] == "ProbeFiles"
    assert len(result["files"]) == 4

    # Check existence
    assert result["files"][0]["exists"] is True
    assert result["files"][0]["size"] > 0
    assert result["files"][0]["creation_time"] > 0
    assert result["files"][3]["exists"] is False


def test_run_probe_files_file_existence(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    f1 = tmp_path / "existing.mp4"
    f1.write_text("dummy")

    payload = {
        "target_dir": str(target_dir),
        "files": [
            {"scene_id": 1, "path": str(f1)},
            {"scene_id": 2, "path": str(tmp_path / "missing.mp4")}
        ]
    }

    result = task.run_probe_files(payload)
    assert result["status"] == "success"
    assert len(result["files"]) == 2
    assert result["files"][0]["exists"] is True
    assert result["files"][1]["exists"] is False


def test_run_probe_files_duplicate_detection(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    dirA = tmp_path / "dirA"
    dirB = tmp_path / "dirB"
    dirA.mkdir()
    dirB.mkdir()

    f1 = dirA / "duplicate_name.mp4"
    f2 = dirB / "DUPLICATE_NAME.mp4"
    f3 = dirA / "unique_name.mp4"
    
    f1.write_text("1")
    f2.write_text("2")
    f3.write_text("3")

    payload = {
        "target_dir": str(target_dir),
        "files": [
            {"scene_id": 1, "path": str(f1)},
            {"scene_id": 2, "path": str(f2)},
            {"scene_id": 3, "path": str(f3)}
        ]
    }

    result = task.run_probe_files(payload)
    assert result["status"] == "success"
    assert len(result["files"]) == 3

    # Check duplicates detection
    assert result["files"][0]["is_duplicate_name"] is True
    assert result["files"][1]["is_duplicate_name"] is True
    assert result["files"][2]["is_duplicate_name"] is False
    assert result["duplicate_count"] == 1


def test_run_probe_files_nested_graphql_format(tmp_path):
    f1 = tmp_path / "nested_scene.mp4"
    f1.write_text("video content")

    payload = {
        "target_dir": str(tmp_path),
        "files": [
            {
                "id": 201,
                "files": [{"path": str(f1)}]
            }
        ]
    }

    result = task.run_probe_files(payload)
    assert result["status"] == "success"
    assert len(result["files"]) == 1
    assert result["files"][0]["exists"] is True
    assert result["files"][0]["scene_id"] == 201
    assert result["files"][0]["basename"] == "nested_scene.mp4"


def test_run_build_megapack(tmp_path):
    output_dir = tmp_path / "output_pack"
    pack_title = "Test_Pack_101"
    pack_dir = output_dir / pack_title
    pack_dir.mkdir(parents=True, exist_ok=True)
    f1 = pack_dir / "video1.mp4"
    f1.write_text("test video payload")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [
            {
                "id": 1,
                "title": "Scene 1",
                "path": str(f1),
                "performers": ["Performer A"],
                "tags": ["Tag 1"]
            }
        ],
        "notes": "Custom test notes"
    }

    result = task.run_build_megapack(payload)

    assert result["status"] == "success"
    assert result["pack_title"] == "Test_Pack_101"
    assert os.path.exists(result["torrent_path"])
    assert os.path.exists(result["manifest_path"])
    assert "Performer A" in result["bbcode"]
    assert "Custom test notes" in result["bbcode"]

    # Verify manifest JSON content
    with open(result["manifest_path"], "r", encoding="utf-8") as mf:
        manifest_data = json.load(mf)
        assert manifest_data["pack_title"] == "Test_Pack_101"
        assert manifest_data["scene_count"] == 1


def test_run_build_megapack_top_level_metadata(tmp_path):
    output_dir = tmp_path / "output_pack_toplevel"
    pack_title = "Test_Pack_TopLevel"
    pack_dir = output_dir / pack_title
    pack_dir.mkdir(parents=True, exist_ok=True)
    f1 = pack_dir / "video1.mp4"
    f1.write_text("test video payload")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "performers": [{"name": "Top Performer"}],
        "tags": [{"name": "Top Tag"}],
        "scenes": [
            {
                "id": 1,
                "title": "Scene 1",
                "path": str(f1),
                "performers": ["Scene Performer"],
                "tags": ["Scene Tag"]
            }
        ]
    }

    result = task.run_build_megapack(payload)
    assert "Top Performer" in result["bbcode"]
    assert "Top Tag" in result["bbcode"]


def test_run_build_megapack_special_chars_in_title(tmp_path):
    output_dir = tmp_path / "output_pack_special"
    pack_title = "Test: Pack / 2026 * Special"
    pack_dir = output_dir / task.sanitize_name(pack_title)
    pack_dir.mkdir(parents=True, exist_ok=True)
    f1 = pack_dir / "video_special.mp4"
    f1.write_text("video payload")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [{"id": 1, "path": str(f1)}]
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"
    assert os.path.exists(result["torrent_path"])
    assert os.path.exists(result["manifest_path"])


def test_concurrent_build_protection(tmp_path):
    output_dir = tmp_path / "locked_pack"
    output_dir.mkdir(parents=True, exist_ok=True)
    pack_title = "Test_Locked_Pack"
    lock_file = output_dir / ".Test_Locked_Pack.lock"
    # Write current PID so is_pid_running returns True
    lock_file.write_text(f"pid={os.getpid()}\nstarted={time.time()}\n")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, pack_title)}]
    }

    with pytest.raises(RuntimeError, match="Concurrent build in progress"):
        task.run_build_megapack(payload)


def test_stale_lockfile_reclaim(tmp_path):
    output_dir = tmp_path / "stale_locked_pack"
    output_dir.mkdir(parents=True, exist_ok=True)
    pack_title = "Test_Stale_Pack"
    lock_file = output_dir / ".Test_Stale_Pack.lock"
    # Use PID 99999999 which is not running
    lock_file.write_text("pid=99999999\nstarted=1000000.0\n")

    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, pack_title)}]
    }

    result = task.run_build_megapack(payload)
    assert result["status"] == "success"


def test_lockfile_cleanup_on_failure(tmp_path):
    output_dir = tmp_path / "failing_pack"
    output_dir.mkdir(parents=True, exist_ok=True)
    pack_title = "Test_Failing_Pack"
    payload = {
        "pack_title": pack_title,
        "output_dir": str(output_dir),
        "scenes": [{"id": 1, "path": _dummy_media_path(output_dir, pack_title)}]
    }

    lock_file = output_dir / ".Test_Failing_Pack.lock"

    # Patch emit_progress to raise an exception during build
    def mock_emit(prog, msg=None):
        if prog == 0.15:
            raise RuntimeError("Forced build error")

    with patch.object(task, "emit_progress", side_effect=mock_emit):
        with pytest.raises(RuntimeError, match="Forced build error"):
            task.run_build_megapack(payload)

    # Lockfile should be removed in finally block
    assert not os.path.exists(lock_file)


def test_parse_input_payload_json():
    test_json = json.dumps({
        "task_name": "BuildMegapack",
        "server_connection": {"Scheme": "http", "Port": 9999},
        "args": [
            {"key": "mode", "value": "probe"},
            {"key": "payload", "value": json.dumps({"target_dir": "C:\\Packs"})}
        ]
    })

    stdin_capture = io.StringIO(test_json)
    sys.stdin = stdin_capture
    try:
        mode, payload, conn = task.parse_input_payload()
        assert mode == "probe"
        assert payload.get("target_dir") == "C:\\Packs"
        assert conn.get("Port") == 9999
    finally:
        sys.stdin = sys.__stdin__


def test_parse_input_payload_dict_args():
    test_json = json.dumps({
        "task_name": "BuildMegapack",
        "args": {
            "mode": "ProbeFiles",
            "payload": {"target_dir": "D:\\Packs"}
        }
    })

    stdin_capture = io.StringIO(test_json)
    sys.stdin = stdin_capture
    try:
        mode, payload, conn = task.parse_input_payload()
        assert mode == "probe"
        assert payload.get("target_dir") == "D:\\Packs"
    finally:
        sys.stdin = sys.__stdin__


def test_parse_input_payload_cli_args():
    with patch.object(sys, "argv", ["task.py", "probe"]):
        mode, payload, conn = task.parse_input_payload()
        assert mode == "probe"

    with patch.object(sys, "argv", ["task.py", "build"]):
        mode, payload, conn = task.parse_input_payload()
        assert mode == "build"


# ---------------------------------------------------------------------------
# Milestone B1: Stderr thread locking, run-id prefixing, sentinels, heartbeat
# ---------------------------------------------------------------------------
import threading
from empornium_megapack.config import Settings


def test_stderr_write_module_level_lock_exists():
    """Verify task module defines a threading.Lock instance guarding stderr."""
    assert hasattr(task, "_stderr_lock"), "task.py must define module-level _stderr_lock"
    # Duck-type check for lock acquire/release or LockType
    assert hasattr(task._stderr_lock, "acquire") and hasattr(task._stderr_lock, "release")


def test_stderr_write_thread_safe_no_interleaving(monkeypatch):
    """Verify concurrent writes through _stderr_write never interleave lines."""
    captured_lines = []
    real_write = captured_lines.append

    # Use custom buffer to record writes
    monkeypatch.setattr(task.sys.stderr, "write", real_write)
    monkeypatch.setattr(task.sys.stderr, "flush", lambda: None)

    def worker(tid):
        for i in range(20):
            line = f"\x01i\x02[Thread-{tid}] message number {i}\n"
            task._stderr_write(line)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every write should be a complete atomic line ending in newline
    assert len(captured_lines) == 100
    for line in captured_lines:
        assert line.startswith("\x01i\x02[Thread-")
        assert line.endswith("\n")


def test_emit_progress_with_active_run_id(monkeypatch, capsys):
    """emit_progress prefixes human-readable messages with [emp:<run_id>] while preserving numeric protocol."""
    task._active_run_id = "run-test-prefix-123"
    try:
        task.emit_progress(0.42, "Extracting video frames")
        err = capsys.readouterr().err
        # Numeric progress line must remain strictly untouched
        assert "\x01p\x020.4200\n" in err
        # Info log message must have run-id prefix
        assert "\x01i\x02[emp:run-test-prefix-123] [42%] Extracting video frames\n" in err
    finally:
        task._active_run_id = None


def test_emit_progress_without_active_run_id(capsys):
    """emit_progress without active run_id does not emit [emp: prefix."""
    task._active_run_id = None
    task.emit_progress(0.15, "Validating inputs")
    err = capsys.readouterr().err
    assert "\x01p\x020.1500\n" in err
    assert "\x01i\x02[15%] Validating inputs\n" in err
    assert "[emp:" not in err


def test_sentinel_emitters_byte_identical_and_exclude_run_id_prefix(capsys):
    """Sentinel lines (RESULT, BBCODE, FAILED) MUST NOT have [emp:<run_id>] prefix."""
    run_id = "run-strict-sentinel-999"
    task._active_run_id = run_id
    try:
        # 1. EMPORNIUM_TASK_RESULT
        result_payload = {"run_id": run_id}
        result_dict = {"status": "success", "bbcode": "test_bbcode"}
        task.emit_result_sentinel(result_payload, result_dict)
        err = capsys.readouterr().err
        assert f"\x01i\x02EMPORNIUM_TASK_RESULT {run_id}: " in err
        assert f"[emp:{run_id}] EMPORNIUM_TASK_RESULT" not in err
        assert "[emp:" not in err

        # 2. EMPORNIUM_TASK_BBCODE
        task.emit_bbcode_sentinel(result_payload, result_dict)
        err = capsys.readouterr().err
        assert f"\x01i\x02EMPORNIUM_TASK_BBCODE {run_id} 1/1: " in err
        assert f"[emp:{run_id}] EMPORNIUM_TASK_BBCODE" not in err
        assert "[emp:" not in err

        # 3. EMPORNIUM_TASK_FAILED
        # Direct check of sentinel write in failure block
        task._stderr_write(f"\x01e\x02EMPORNIUM_TASK_FAILED {run_id}: Missing video\n")
        err = capsys.readouterr().err
        assert f"\x01e\x02EMPORNIUM_TASK_FAILED {run_id}: Missing video\n" in err
        assert f"[emp:{run_id}] EMPORNIUM_TASK_FAILED" not in err
    finally:
        task._active_run_id = None


def test_upload_previews_narration_start_and_success(tmp_path, monkeypatch, capsys):
    """upload_previews emits upload start with byte size, success with elapsed seconds, and threads log_callback."""
    img = tmp_path / "sheet_01.jpg"
    img.write_bytes(b"\xff\xd8" + b"X" * 1024)
    run_id = "run-narration-preview-1"
    task._active_run_id = run_id

    def fake_upload(image_path, settings=None, log_callback=None):
        if log_callback:
            log_callback("attempt 1/3 failed (HTTP 500), retrying in 0.5s")
        return "https://img.example/sheet_01.jpg"

    monkeypatch.setattr(task._domain_images, "upload_hamster", fake_upload)
    monkeypatch.setattr(task._domain_config, "get_settings", lambda: Settings(hamster_api_key="valid-key"))

    try:
        urls = task.upload_previews([str(img)], config={"upload_previews": True})
        assert urls == ["https://img.example/sheet_01.jpg"]
        err = capsys.readouterr().err

        # Start narration line
        assert f"\x01i\x02[emp:{run_id}] [Upload] Starting sheet_01.jpg (1026 bytes) -> HamsterImg\n" in err
        # Threaded retry line from upload_hamster
        assert f"\x01w\x02[emp:{run_id}] [Upload] sheet_01.jpg: attempt 1/3 failed (HTTP 500), retrying in 0.5s\n" in err
        # Success narration line with elapsed time and URL
        assert f"\x01i\x02[emp:{run_id}] [Upload] sheet_01.jpg uploaded in " in err
        assert "-> https://img.example/sheet_01.jpg\n" in err
    finally:
        task._active_run_id = None


def test_upload_previews_fallback_to_file_diagnostic(tmp_path, monkeypatch, capsys):
    """upload_previews logs fallback-to-file diagnostics with [emp:<run_id>] prefix."""
    img = tmp_path / "failing_sheet.jpg"
    img.write_bytes(b"\xff\xd8" + b"Y" * 500)
    run_id = "run-fallback-diag"
    task._active_run_id = run_id

    def failing_upload(image_path, settings=None, log_callback=None):
        raise task._domain_images.ContactSheetError("HamsterImg service unavailable")

    monkeypatch.setattr(task._domain_images, "upload_hamster", failing_upload)
    monkeypatch.setattr(task._domain_config, "get_settings", lambda: Settings(hamster_api_key="valid-key"))

    try:
        urls = task.upload_previews([str(img)], config={"upload_previews": True})
        assert len(urls) == 1
        assert urls[0].startswith("file:///")
        err = capsys.readouterr().err
        assert f"\x01w\x02[emp:{run_id}] Failed to upload contact sheet 'failing_sheet.jpg'" in err
        assert "HamsterImg service unavailable" in err
        assert "Falling back to local preview URL" in err
    finally:
        task._active_run_id = None


def test_daemon_heartbeat_thread_lifecycle(capsys):
    """Heartbeat context emits status periodically via daemon thread and stops cleanly."""
    run_id = "run-heartbeat-test"
    task._active_run_id = run_id
    try:
        with task.heartbeat("Contact sheet rendering", interval=0.03):
            time.sleep(0.08)  # Wait for ~2 ticks
        err = capsys.readouterr().err
        assert f"\x01i\x02[emp:{run_id}] [Heartbeat] Contact sheet rendering still running" in err
    finally:
        task._active_run_id = None

