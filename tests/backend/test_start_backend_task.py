"""
Unit tests for StartBackend task handler and lifecycle functions in plugin/task.py.
"""

import sys
import os
import io
import json
import time
import socket
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add plugin dir to sys.path
PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task


def test_get_sidecar_port_precedence(monkeypatch):
    # 1. EMPORNIUM_PORT takes highest precedence
    monkeypatch.setenv("EMPORNIUM_PORT", "12345")
    assert task.get_sidecar_port() == 12345

    # 2. Invalid EMPORNIUM_PORT falls back
    monkeypatch.setenv("EMPORNIUM_PORT", "not_a_number")
    port = task.get_sidecar_port()
    assert port == 9941

    # 3. When EMPORNIUM_PORT is unset
    monkeypatch.delenv("EMPORNIUM_PORT", raising=False)
    assert task.get_sidecar_port() == 9941

    # 4. When _domain_config is None
    monkeypatch.setattr(task, "_domain_config", None)
    assert task.get_sidecar_port() == 9941


def test_get_plugin_build_stamp(tmp_path, monkeypatch):
    # Dev checkout: no stamp file in CURRENT_DIR
    monkeypatch.setattr(task, "CURRENT_DIR", tmp_path)
    assert task.get_plugin_build_stamp() is None

    # Packaged layout with BUILD_STAMP
    stamp_file = tmp_path / "BUILD_STAMP"
    stamp_file.write_text("1.0.0-639bc89\n", encoding="utf-8")
    assert task.get_plugin_build_stamp() == "1.0.0-639bc89"

    # Packaged layout with lowercase build_stamp
    stamp_file.unlink()
    stamp_file_lower = tmp_path / "build_stamp"
    stamp_file_lower.write_text("0.2.0-deadbeef\n", encoding="utf-8")
    assert task.get_plugin_build_stamp() == "0.2.0-deadbeef"


def test_check_sidecar_health_healthy(monkeypatch):
    mock_resp_data = {
        "status": "ok",
        "track": "Empornium Megapack Builder",
        "version": "0.2.0",
        "build_stamp": "0.2.0-639bc89",
    }
    
    class FakeResponse:
        status = 200
        def read(self):
            return json.dumps(mock_resp_data).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        assert req.get_header("Host") == "127.0.0.1:9941"
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    is_healthy, health_dict = task.check_sidecar_health(9941)
    assert is_healthy is True
    assert health_dict == mock_resp_data


def test_check_sidecar_health_unhealthy_or_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    is_healthy, health_dict = task.check_sidecar_health(9941)
    assert is_healthy is False
    assert health_dict is None


def test_shutdown_sidecar_success(monkeypatch):
    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        assert req.get_header("Host") == "127.0.0.1:9941"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_method() == "POST"
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert task.shutdown_sidecar(9941) is True


def test_shutdown_sidecar_error_returns_false(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert task.shutdown_sidecar(9941) is False


def test_wait_for_port_release_success(monkeypatch):
    # Port is released immediately (connect_ex != 0)
    fake_socket = MagicMock()
    fake_socket.connect_ex.return_value = 10061  # WSAECONNREFUSED / not connected
    fake_socket.__enter__.return_value = fake_socket

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: fake_socket)
    task.wait_for_port_release(9941, timeout=1.0)


def test_wait_for_port_release_timeout(monkeypatch):
    # Port stays open (connect_ex == 0) until timeout
    fake_socket = MagicMock()
    fake_socket.connect_ex.return_value = 0  # connected
    fake_socket.__enter__.return_value = fake_socket

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: fake_socket)
    with pytest.raises(RuntimeError, match="Port 9941 failed to release within"):
        task.wait_for_port_release(9941, timeout=0.2)


def test_spawn_sidecar_missing_venv_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "CURRENT_DIR", tmp_path)
    monkeypatch.delenv("EMPORNIUM_VENV", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        task.spawn_sidecar(9941)

    assert "Virtual environment not found" in str(excinfo.value)
    assert "install.ps1" in str(excinfo.value)
    assert "install.sh" in str(excinfo.value)


def test_spawn_sidecar_spawns_detached_process(tmp_path, monkeypatch):
    fake_venv = tmp_path / ".venv"
    if os.name == "nt":
        fake_py = fake_venv / "Scripts" / "python.exe"
    else:
        fake_py = fake_venv / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.write_text("", encoding="utf-8")

    monkeypatch.setattr(task, "CURRENT_DIR", tmp_path)
    monkeypatch.delenv("EMPORNIUM_VENV", raising=False)

    spawned = []

    def fake_popen(cmd, cwd=None, env=None, **kwargs):
        spawned.append({"cmd": cmd, "cwd": cwd, "env": env, "kwargs": kwargs})
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    task.spawn_sidecar(9941)

    assert len(spawned) == 1
    call = spawned[0]
    assert call["cmd"][0] == str(fake_py)
    assert "-m" in call["cmd"]
    assert "uvicorn" in call["cmd"]
    assert "empornium_megapack.main:app" in call["cmd"]
    assert "--port" in call["cmd"]
    assert "9941" in call["cmd"]
    assert call["kwargs"]["stdin"] == subprocess.DEVNULL
    assert call["kwargs"]["stdout"] == subprocess.DEVNULL
    assert call["kwargs"]["stderr"] == subprocess.DEVNULL
    assert call["kwargs"]["close_fds"] is True

    if os.name == "nt":
        flags = call["kwargs"].get("creationflags", 0)
        assert flags & 0x00000008  # DETACHED_PROCESS
        assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert call["kwargs"].get("start_new_session") is True


def test_run_start_backend_adopts_matching_stamp(monkeypatch):
    monkeypatch.setattr(task, "get_sidecar_port", lambda: 9941)
    monkeypatch.setattr(task, "check_sidecar_health", lambda port: (True, {"build_stamp": "0.2.0-639bc89"}))
    monkeypatch.setattr(task, "get_plugin_build_stamp", lambda: "0.2.0-639bc89")

    spawn_called = False
    def fake_spawn(port):
        nonlocal spawn_called
        spawn_called = True

    monkeypatch.setattr(task, "spawn_sidecar", fake_spawn)

    result = task.run_start_backend({})
    assert result["status"] == "ok"
    assert result["action"] == "adopted"
    assert result["port"] == 9941
    assert result["build_stamp"] == "0.2.0-639bc89"
    assert not spawn_called


def test_run_start_backend_adopts_in_dev_checkout(monkeypatch):
    monkeypatch.setattr(task, "get_sidecar_port", lambda: 9941)
    monkeypatch.setattr(task, "check_sidecar_health", lambda port: (True, {"build_stamp": "some-running-stamp"}))
    monkeypatch.setattr(task, "get_plugin_build_stamp", lambda: None)  # Dev checkout

    spawn_called = False
    def fake_spawn(port):
        nonlocal spawn_called
        spawn_called = True

    monkeypatch.setattr(task, "spawn_sidecar", fake_spawn)

    result = task.run_start_backend({})
    assert result["status"] == "ok"
    assert result["action"] == "adopted"
    assert result["port"] == 9941
    assert not spawn_called


def test_run_start_backend_replaces_mismatched_stamp(monkeypatch):
    monkeypatch.setattr(task, "get_sidecar_port", lambda: 9941)
    monkeypatch.setattr(task, "check_sidecar_health", lambda port: (True, {"build_stamp": "old-stamp-123"}))
    monkeypatch.setattr(task, "get_plugin_build_stamp", lambda: "new-stamp-456")

    shutdown_called = False
    def fake_shutdown(port):
        nonlocal shutdown_called
        shutdown_called = True
        return True

    wait_called = False
    def fake_wait(port, timeout=10.0):
        nonlocal wait_called
        wait_called = True

    spawn_called = False
    def fake_spawn(port):
        nonlocal spawn_called
        spawn_called = True

    monkeypatch.setattr(task, "shutdown_sidecar", fake_shutdown)
    monkeypatch.setattr(task, "wait_for_port_release", fake_wait)
    monkeypatch.setattr(task, "spawn_sidecar", fake_spawn)

    result = task.run_start_backend({})
    assert result["status"] == "ok"
    assert result["action"] == "started"
    assert result["port"] == 9941
    assert shutdown_called is True
    assert wait_called is True
    assert spawn_called is True


def test_run_start_backend_starts_when_unhealthy(monkeypatch):
    monkeypatch.setattr(task, "get_sidecar_port", lambda: 9941)
    monkeypatch.setattr(task, "check_sidecar_health", lambda port: (False, None))

    spawn_called = False
    def fake_spawn(port):
        nonlocal spawn_called
        spawn_called = True

    monkeypatch.setattr(task, "spawn_sidecar", fake_spawn)

    result = task.run_start_backend({})
    assert result["status"] == "ok"
    assert result["action"] == "started"
    assert result["port"] == 9941
    assert spawn_called is True


def test_parse_input_payload_start_backend_variations(monkeypatch):
    # 1. sys.argv variations
    monkeypatch.setattr(sys, "argv", ["task.py", "start_backend"])
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"

    monkeypatch.setattr(sys, "argv", ["task.py", "startbackend"])
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"

    monkeypatch.setattr(sys, "argv", ["task.py", "Start-Backend"])
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"

    # 2. JSON task_name in stdin
    monkeypatch.setattr(sys, "argv", ["task.py"])
    fake_stdin = io.StringIO(json.dumps({"task_name": "StartBackend"}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"

    # 3. JSON args list in stdin
    fake_stdin = io.StringIO(json.dumps({
        "task_name": "CustomName",
        "args": [{"key": "mode", "value": {"str": "start_backend"}}]
    }))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"

    # 4. JSON args dict in stdin
    fake_stdin = io.StringIO(json.dumps({
        "args": {"mode": "start_backend"}
    }))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    mode, _, _ = task.parse_input_payload()
    assert mode == "start_backend"


def test_main_start_backend_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(task, "check_dependencies", lambda: None)
    monkeypatch.setattr(task, "parse_input_payload", lambda: ("start_backend", {}, {}))
    monkeypatch.setattr(
        task,
        "run_start_backend",
        lambda payload: {"status": "ok", "action": "started", "port": 9941},
    )
    monkeypatch.setattr(task, "post_result_to_sidecar", lambda payload, result: None)
    monkeypatch.setattr(task, "emit_result_sentinel", lambda payload, result: None)
    monkeypatch.setattr(task, "emit_bbcode_sentinel", lambda payload, result: None)

    with pytest.raises(SystemExit) as excinfo:
        task.main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["status"] == "ok"
    assert result["action"] == "started"
    assert result["port"] == 9941


def test_task_importable_when_backend_absent(monkeypatch):
    monkeypatch.setattr(task, "_domain_config", None)
    monkeypatch.setattr(task, "_domain_images", None)
    monkeypatch.setattr(task, "_domain_torrents", None)

    # get_sidecar_port still resolves to default 9941
    assert task.get_sidecar_port() == 9941
    assert callable(task.check_sidecar_health)
    assert callable(task.shutdown_sidecar)
    assert callable(task.wait_for_port_release)
    assert callable(task.spawn_sidecar)
    assert callable(task.run_start_backend)


