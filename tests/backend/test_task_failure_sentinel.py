import json
import subprocess
import sys
from pathlib import Path
import pytest

TASK_PY = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"


def test_task_parse_input_payload_extracts_run_id(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("plugin_task", str(TASK_PY))
    task_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(task_module)

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
    """When a task fails, task.py writes \\x01e\\x02DEEPSEEK_TASK_FAILED <run_id>: <err> to stderr."""
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
    expected_sentinel = f"\x01e\x02DEEPSEEK_TASK_FAILED {run_id}:"
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
    assert "\x01e\x02DEEPSEEK_TASK_FAILED:" in result.stderr
    assert "\x01e\x02Task execution failed:" in result.stderr
