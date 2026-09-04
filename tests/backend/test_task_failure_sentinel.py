import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

TASK_PY = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"


@pytest.fixture(autouse=True)
def reset_stderr_latch():
    yield
    for mod_name in list(sys.modules.keys()):
        if "task" in mod_name:
            mod = sys.modules[mod_name]
            if hasattr(mod, "_stderr_broken"):
                mod._stderr_broken = False


def _load_task_module():
    spec = importlib.util.spec_from_file_location("plugin_task", str(TASK_PY))
    task_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(task_module)
    task_module._stderr_broken = False
    return task_module


def test_task_parse_input_payload_extracts_run_id(monkeypatch):
    task_module = _load_task_module()

    # Case 1: args as list with JSON payload containing run_id
    payload_dict = {"run_id": "nonce-abc-123", "scenes": []}
    mock_stdin = json.dumps({
        "task_name": "BuildMegapack",
        "args": [
            {"key": "mode", "value": {"str": "build"}},
            {"key": "payload", "value": {"str": json.dumps(payload_dict)}}
        ]
    })
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin.read", lambda: mock_stdin)
    monkeypatch.setattr("sys.argv", ["task.py"])

    mode, payload, conn = task_module.parse_input_payload()
    assert mode == "build"
    assert payload.get("run_id") == "nonce-abc-123"

    # Case 2: args as list with separate run_id key
    mock_stdin_2 = json.dumps({
        "task_name": "BuildMegapack",
        "args": [
            {"key": "mode", "value": {"str": "build"}},
            {"key": "run_id", "value": {"str": "nonce-separate-456"}},
            {"key": "payload", "value": {"str": json.dumps({"pack_title": "Test"})}}
        ]
    })
    monkeypatch.setattr("sys.stdin.read", lambda: mock_stdin_2)
    mode2, payload2, conn2 = task_module.parse_input_payload()
    assert payload2.get("run_id") == "nonce-separate-456"
    assert payload2.get("pack_title") == "Test"

    # Case 3: args as dict
    mock_stdin_3 = json.dumps({
        "task_name": "BuildSingleScene",
        "args": {
            "mode": "single",
            "run_id": "nonce-dict-789",
            "payload": {"pack_title": "Single"}
        }
    })
    monkeypatch.setattr("sys.stdin.read", lambda: mock_stdin_3)
    mode3, payload3, conn3 = task_module.parse_input_payload()
    assert mode3 == "single"
    assert payload3.get("run_id") == "nonce-dict-789"


def test_task_failure_emits_attributable_sentinel_with_run_id():
    """When a task fails, task.py writes \\x01e\\x02EMPORNIUM_TASK_FAILED <run_id>: <err> to stderr."""
    run_id = "test-nonce-fail-999"
    input_data = json.dumps({
        "task_name": "BuildMegapack",
        "args": [
            {"key": "mode", "value": {"str": "build"}},
            {"key": "payload", "value": {"str": json.dumps({
                "run_id": run_id,
                "scenes": [],  # Empty scenes causes build failure
                "output_dir": "C:\\NonExistent"
            })}}
        ]
    })

    result = subprocess.run(
        [sys.executable, str(TASK_PY)],
        input=input_data,
        text=True,
        capture_output=True
    )

    assert result.returncode == 1, f"Expected returncode 1, got {result.returncode}"
    expected_sentinel = f"\x01e\x02EMPORNIUM_TASK_FAILED {run_id}:"
    assert expected_sentinel in result.stderr, (
        f"Expected sentinel '{expected_sentinel}' in stderr. Got:\n{result.stderr}"
    )
    assert "\x01e\x02Task execution failed:" in result.stderr


def test_task_failure_without_run_id_handles_gracefully():
    """When run_id is absent, task failure still emits sentinel and exits 1 cleanly without crashing."""
    input_data = json.dumps({
        "task_name": "BuildMegapack",
        "args": [
            {"key": "mode", "value": {"str": "build"}},
            {"key": "payload", "value": {"str": json.dumps({
                "scenes": [],
                "output_dir": "C:\\NonExistent"
            })}}
        ]
    })

    result = subprocess.run(
        [sys.executable, str(TASK_PY)],
        input=input_data,
        text=True,
        capture_output=True
    )

    assert result.returncode == 1
    assert "\x01e\x02EMPORNIUM_TASK_FAILED:" in result.stderr
    assert "\x01e\x02Task execution failed:" in result.stderr


def test_post_build_reporting_failure_exits_zero_without_fail_result(monkeypatch, capsys):
    """When the build succeeded and post_result_to_sidecar returned True, but emit_result_sentinel raises,
    main() must exit 0 and must NOT post a fail_result to the sidecar."""
    task_module = _load_task_module()
    try:
        run_id = "test-nonce-headline-g"
        payload_in = {"run_id": run_id, "pack_title": "Completed Megapack"}
        success_result = {"status": "success", "pack_title": "Completed Megapack", "uploaded_urls": []}

        monkeypatch.setattr(task_module, "check_dependencies", lambda: None)
        monkeypatch.setattr(task_module, "parse_input_payload", lambda: ("build", payload_in, {}))
        monkeypatch.setattr(task_module, "run_build_megapack", lambda payload, conn: success_result)

        posted_results = []

        def mock_post(payload, result):
            posted_results.append((payload, result))
            return True  # 2xx from run store

        monkeypatch.setattr(task_module, "post_result_to_sidecar", mock_post)

        def failing_emit_result_sentinel(payload, result):
            raise OSError(22, "[Errno 22] Invalid argument")

        monkeypatch.setattr(task_module, "emit_result_sentinel", failing_emit_result_sentinel)

        with pytest.raises(SystemExit) as excinfo:
            task_module.main()

        assert excinfo.value.code == 0, f"Expected exit code 0, got {excinfo.value.code}"
        assert len(posted_results) == 1, f"Expected exactly 1 post, got {len(posted_results)}"
        assert posted_results[0][1]["status"] == "success"
        err = capsys.readouterr().err
        assert "EMPORNIUM_TASK_FAILED" not in err
        assert "Task completed, but result reporting failed (result is in the sidecar run store)" in err
    finally:
        task_module._stderr_broken = False


def test_genuine_pre_build_failure_posts_fail_result_and_exits_one(monkeypatch, capsys):
    """A genuine failure BEFORE the build completes still posts a fail_result and exits 1."""
    task_module = _load_task_module()
    try:
        run_id = "test-nonce-genuine-h"
        payload_in = {"run_id": run_id, "pack_title": "Failing Megapack"}

        monkeypatch.setattr(task_module, "check_dependencies", lambda: None)
        monkeypatch.setattr(task_module, "parse_input_payload", lambda: ("build", payload_in, {}))

        def failing_build(payload, conn):
            raise RuntimeError("Genuine build error: missing required files")

        monkeypatch.setattr(task_module, "run_build_megapack", failing_build)

        posted_results = []

        def mock_post(payload, result):
            posted_results.append((payload, result))
            return True

        monkeypatch.setattr(task_module, "post_result_to_sidecar", mock_post)

        with pytest.raises(SystemExit) as excinfo:
            task_module.main()

        assert excinfo.value.code == 1, f"Expected exit code 1, got {excinfo.value.code}"
        assert len(posted_results) == 1, f"Expected exactly 1 post, got {len(posted_results)}"
        fail_payload, fail_result = posted_results[0]
        assert fail_result["status"] == "failed"
        assert fail_result["run_id"] == run_id
        assert "Genuine build error: missing required files" in fail_result["error"]
        err = capsys.readouterr().err
        assert f"\x01e\x02EMPORNIUM_TASK_FAILED {run_id}:" in err
        assert "Genuine build error: missing required files" in err
    finally:
        task_module._stderr_broken = False

