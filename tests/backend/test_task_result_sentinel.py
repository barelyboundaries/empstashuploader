import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("plugin_task_result", str(TASK_PY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._stderr_broken = False
    return module


def _emit(payload, result, capsys):
    task = _load_task_module()
    task.emit_result_sentinel(payload, result)
    return capsys.readouterr().err


def _parse(stderr, run_id):
    marker = f"\x01i\x02EMPORNIUM_TASK_RESULT {run_id}: "
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

    sentinel_lines = [ln for ln in stderr.splitlines() if "EMPORNIUM_TASK_RESULT" in ln]
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

    assert "EMPORNIUM_TASK_RESULT" not in stderr


def test_emitter_never_raises_on_unserializable_result(capsys):
    """A reporting failure must not fail an otherwise successful build."""
    task = _load_task_module()
    task.emit_result_sentinel({"run_id": "test-nonce-result-005"}, {"bad": object()})

    err = capsys.readouterr().err
    assert "EMPORNIUM_TASK_RESULT" not in err
    assert "Failed to emit result sentinel" in err


def test_oversized_result_sheds_bbcode_first_and_respects_max_chars(capsys):
    """A result whose JSON exceeds 30000 chars sheds bbcode first and stays within cap."""
    run_id = "test-nonce-shed-bbcode"
    result = {
        "status": "success",
        "pack_title": "Oversized Pack",
        "bbcode": "X" * 35000,
        "uploaded_urls": ["https://hamsterimg.net/a.jpg"],
        "image_urls": ["https://hamsterimg.net/thumb.jpg"],
        "scenes": [{"id": 1}],
    }

    stderr = _emit({"run_id": run_id}, result, capsys)
    marker = f"\x01i\x02EMPORNIUM_TASK_RESULT {run_id}: "
    assert marker in stderr

    sentinel_lines = [ln for ln in stderr.splitlines() if marker in ln]
    assert len(sentinel_lines) == 1
    raw_line = sentinel_lines[0]
    encoded_json = raw_line[raw_line.index(marker) + len(marker):]

    assert len(encoded_json) <= 30000
    assert len(raw_line) <= 30000 + len(marker)

    parsed = _parse(stderr, run_id)
    assert "bbcode" not in parsed
    assert parsed["bbcode_truncated"] is True
    assert parsed["sentinel_shed_keys"] == ["bbcode"]
    assert parsed["uploaded_urls"] == ["https://hamsterimg.net/a.jpg"]
    assert parsed["image_urls"] == ["https://hamsterimg.net/thumb.jpg"]
    assert parsed["scenes"] == [{"id": 1}]


def test_oversized_result_after_all_shedding_emits_stub(capsys):
    """A result still oversized after all shedding emits a minimal stub with sentinel_truncated True."""
    run_id = "test-nonce-shed-stub"
    result = {
        "status": "success",
        "task": "BuildMegapack",
        "run_id": run_id,
        "bbcode": "Y" * 5000,
        "uploaded_urls": ["https://hamsterimg.net/a.jpg"],
        "image_urls": ["https://hamsterimg.net/thumb.jpg"],
        "scenes": [{"id": 1}],
        "huge_non_sheddable_data": "Z" * 35000,
    }

    stderr = _emit({"run_id": run_id}, result, capsys)
    marker = f"\x01i\x02EMPORNIUM_TASK_RESULT {run_id}: "
    assert marker in stderr

    sentinel_lines = [ln for ln in stderr.splitlines() if marker in ln]
    assert len(sentinel_lines) == 1
    raw_line = sentinel_lines[0]
    encoded_json = raw_line[raw_line.index(marker) + len(marker):]

    assert len(encoded_json) <= 30000
    assert len(encoded_json) < 1000

    parsed = _parse(stderr, run_id)
    assert parsed["sentinel_truncated"] is True
    assert parsed["status"] == "success"
    assert parsed["task"] == "BuildMegapack"
    assert parsed["run_id"] == run_id
    assert parsed["bbcode_truncated"] is True
    assert "huge_non_sheddable_data" not in parsed


def test_result_under_cap_emitted_unchanged(capsys):
    """A result already under the cap is emitted unchanged with no truncation keys added."""
    run_id = "test-nonce-under-cap"
    result = {
        "status": "success",
        "pack_title": "Normal Pack",
        "bbcode": "[img]https://hamsterimg.net/pic.jpg[/img]",
        "uploaded_urls": ["https://hamsterimg.net/pic.jpg"],
        "scenes": [{"id": 10}],
    }

    stderr = _emit({"run_id": run_id}, result, capsys)
    parsed = _parse(stderr, run_id)

    assert "sentinel_shed_keys" not in parsed
    assert "bbcode_truncated" not in parsed
    assert "sentinel_truncated" not in parsed
    assert parsed["status"] == "success"
    assert parsed["pack_title"] == "Normal Pack"
    assert parsed["bbcode"] == "[img]https://hamsterimg.net/pic.jpg[/img]"
    assert parsed["uploaded_urls"] == ["https://hamsterimg.net/pic.jpg"]
    assert parsed["scenes"] == [{"id": 10}]


def test_stderr_write_latches_on_oserror_22(monkeypatch):
    """_stderr_write returns False, latches _stderr_broken on OSError(22), and stops calling write."""
    task = _load_task_module()
    task._stderr_broken = False
    try:
        call_count = 0

        def mock_write(text):
            nonlocal call_count
            call_count += 1
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(sys.stderr, "write", mock_write)

        first_result = task._stderr_write("first line\n")
        assert first_result is False
        assert task._stderr_broken is True
        assert call_count == 1

        second_result = task._stderr_write("second line\n")
        assert second_result is False
        assert call_count == 1
    finally:
        task._stderr_broken = False


def test_emit_result_sentinel_does_not_raise_on_oserror_22(monkeypatch):
    """emit_result_sentinel does not raise when stderr raises OSError(22) on every write."""
    task = _load_task_module()
    task._stderr_broken = False
    try:
        write_mock = MagicMock(side_effect=OSError(22, "Invalid argument"))
        monkeypatch.setattr(sys.stderr, "write", write_mock)

        task.emit_result_sentinel(
            {"run_id": "test-nonce-res-oserror"},
            {"status": "success", "bbcode": "test content", "uploaded_urls": []},
        )
        assert task._stderr_broken is True
    finally:
        task._stderr_broken = False


def test_emit_bbcode_sentinel_stops_after_first_failed_chunk(monkeypatch):
    """emit_bbcode_sentinel stops after the first failed chunk write instead of attempting all chunks."""
    task = _load_task_module()
    task._stderr_broken = False
    try:
        write_mock = MagicMock(return_value=False)
        monkeypatch.setattr(task, "_stderr_write", write_mock)

        payload = {"run_id": "test-nonce-bbcode-chunks"}
        result = {"status": "success", "bbcode": "x" * 12000}

        task.emit_bbcode_sentinel(payload, result)

        assert write_mock.call_count == 1
        first_call_arg = write_mock.call_args[0][0]
        assert "EMPORNIUM_TASK_BBCODE" in first_call_arg
        assert "1/4:" in first_call_arg
    finally:
        task._stderr_broken = False

