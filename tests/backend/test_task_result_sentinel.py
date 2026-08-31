import importlib.util
import json
from pathlib import Path

TASK_PY = Path(__file__).resolve().parent.parent.parent / "plugin" / "task.py"


def _load_task_module():
    spec = importlib.util.spec_from_file_location("plugin_task_result", str(TASK_PY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _emit(payload, result, capsys):
    task = _load_task_module()
    task.emit_result_sentinel(payload, result)
    return capsys.readouterr().err


def _parse(stderr, run_id):
    marker = f"\x01i\x02DEEPSEEK_TASK_RESULT {run_id}: "
    assert marker in stderr, stderr
    line = stderr[stderr.index(marker) + len(marker):].splitlines()[0]
    return json.loads(line)


def test_result_sentinel_carries_upload_urls_and_bbcode(capsys):
    """The UI can only learn the hosted image URLs from this sentinel."""
    run_id = "test-nonce-result-001"
    result = {
        "status": "success",
        "pack_title": "Example Pack",
        "bbcode": "[img]https://hamsterimg.net/a.jpg[/img]",
        "uploaded_urls": ["https://hamsterimg.net/a.jpg"],
        "tracker_tags": ["big.ass", "pov"],
        "preflight": {"ready": True, "checks": [{"id": "images_remote", "passed": True}]},
        "ready": True,
    }

    parsed = _parse(_emit({"run_id": run_id}, result, capsys), run_id)

    assert parsed["uploaded_urls"] == ["https://hamsterimg.net/a.jpg"]
    assert parsed["bbcode"] == "[img]https://hamsterimg.net/a.jpg[/img]"
    assert parsed["tracker_tags"] == ["big.ass", "pov"]
    assert parsed["preflight"]["ready"] is True


def test_result_sentinel_is_a_single_line(capsys):
    """A multi-line payload would break the log-line parser in review.js."""
    run_id = "test-nonce-result-002"
    result = {"status": "success", "bbcode": "line one\nline two\nline three"}

    stderr = _emit({"run_id": run_id}, result, capsys)

    sentinel_lines = [ln for ln in stderr.splitlines() if "DEEPSEEK_TASK_RESULT" in ln]
    assert len(sentinel_lines) == 1
    assert _parse(stderr, run_id)["bbcode"] == "line one\nline two\nline three"


def test_result_sentinel_omits_bulky_duplicate_fields(capsys):
    run_id = "test-nonce-result-003"
    result = {
        "status": "success",
        "submission_payload": {"description": "x" * 100},
        "contact_sheets": ["C:\\Packs\\sheet.jpg"],
        "uploaded_urls": [],
    }

    parsed = _parse(_emit({"run_id": run_id}, result, capsys), run_id)

    assert "submission_payload" not in parsed
    assert "contact_sheets" not in parsed


def test_oversized_bbcode_is_dropped_rather_than_truncated(capsys):
    """Losing the field cleanly beats emitting JSON the UI cannot parse."""
    run_id = "test-nonce-result-004"
    result = {"status": "success", "bbcode": "x" * 200000, "uploaded_urls": ["https://h.net/a.jpg"]}

    parsed = _parse(_emit({"run_id": run_id}, result, capsys), run_id)

    assert "bbcode" not in parsed
    assert parsed["bbcode_truncated"] is True
    assert parsed["uploaded_urls"] == ["https://h.net/a.jpg"]


def test_no_sentinel_without_a_run_id(capsys):
    """Without a run_id the UI cannot attribute the result, so emit nothing."""
    stderr = _emit({}, {"status": "success"}, capsys)

    assert "DEEPSEEK_TASK_RESULT" not in stderr


def test_emitter_never_raises_on_unserializable_result(capsys):
    """A reporting failure must not fail an otherwise successful build."""
    task = _load_task_module()
    task.emit_result_sentinel({"run_id": "test-nonce-result-005"}, {"bad": object()})

    err = capsys.readouterr().err
    assert "DEEPSEEK_TASK_RESULT" not in err
    assert "Failed to emit result sentinel" in err
