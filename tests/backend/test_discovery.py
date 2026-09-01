"""
Unit tests for the 4-tier ordered backend discovery mechanism in plugin/task.py.
Covers:
Tier 1: EMPORNIUM_BACKEND_DIR environment variable
Tier 2: Active environment / site-packages (importlib.util.find_spec)
Tier 3: Git repository checkout (CURRENT_DIR.parent / "backend")
Tier 4: Vendored fallback directory (CURRENT_DIR / "empornium_megapack")
Tier 5: Fallback when package is not found anywhere
"""

import io
import os
import sys
import importlib
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Ensure plugin dir is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = PROJECT_ROOT / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import task


def test_tier1_discovery_via_env_var(tmp_path, monkeypatch):
    """Tier 1: EMPORNIUM_BACKEND_DIR environment variable override takes top priority."""
    fake_backend = tmp_path / "custom_backend_dir"
    fake_backend.mkdir()
    fake_pkg = fake_backend / "custom_tier1_pkg"
    fake_pkg.mkdir()
    (fake_pkg / "__init__.py").write_text("TIER = 'custom_env_dir'\n", encoding="utf-8")

    monkeypatch.setenv("EMPORNIUM_BACKEND_DIR", str(fake_backend))

    stderr_buf = io.StringIO()
    with patch.object(sys.stderr, "write", stderr_buf.write):
        mod = task.resolve_backend("custom_tier1_pkg")

    assert mod is not None
    assert getattr(mod, "TIER", None) == "custom_env_dir"
    logs = stderr_buf.getvalue()
    assert "\x01i\x02[Discovery] Resolved backend via EMPORNIUM_BACKEND_DIR:" in logs
    assert str(fake_backend) in logs


def test_tier2_discovery_via_site_packages(monkeypatch):
    """Tier 2: Resolves via active Python environment / site-packages."""
    monkeypatch.delenv("EMPORNIUM_BACKEND_DIR", raising=False)

    stderr_buf = io.StringIO()
    with patch.object(sys.stderr, "write", stderr_buf.write):
        mod = task.resolve_backend("empornium_megapack")

    assert mod is not None
    logs = stderr_buf.getvalue()
    assert "\x01i\x02[Discovery] Resolved backend via site-packages/environment:" in logs


def test_tier3_discovery_via_repo_checkout(tmp_path, monkeypatch):
    """Tier 3: Falls back to repository backend directory when site-packages spec is not found."""
    monkeypatch.delenv("EMPORNIUM_BACKEND_DIR", raising=False)

    # Mock find_spec to return None
    with patch("importlib.util.find_spec", return_value=None):
        stderr_buf = io.StringIO()
        with patch.object(sys.stderr, "write", stderr_buf.write):
            mod = task.resolve_backend("empornium_megapack")

        assert mod is not None
        logs = stderr_buf.getvalue()
        assert "\x01i\x02[Discovery] Resolved backend via git repository checkout:" in logs


def test_tier4_discovery_via_vendored_directory(tmp_path, monkeypatch):
    """Tier 4: Falls back to vendored directory inside plugin folder."""
    monkeypatch.delenv("EMPORNIUM_BACKEND_DIR", raising=False)

    fake_plugin_dir = tmp_path / "fake_plugin"
    fake_plugin_dir.mkdir()
    vendored_pkg = fake_plugin_dir / "vendored_test_pkg"
    vendored_pkg.mkdir()
    (vendored_pkg / "__init__.py").write_text("TIER = 'vendored_fallback'\n", encoding="utf-8")

    monkeypatch.setattr(task, "CURRENT_DIR", fake_plugin_dir)

    with patch("importlib.util.find_spec", return_value=None):
        stderr_buf = io.StringIO()
        with patch.object(sys.stderr, "write", stderr_buf.write):
            mod = task.resolve_backend("vendored_test_pkg")

        assert mod is not None
        assert getattr(mod, "TIER", None) == "vendored_fallback"
        logs = stderr_buf.getvalue()
        assert "\x01i\x02[Discovery] Resolved backend via vendored directory:" in logs


def test_discovery_not_found_warning(monkeypatch):
    """Discovery logs a warning with \x01w\x02 when the package is not found in any tier."""
    monkeypatch.delenv("EMPORNIUM_BACKEND_DIR", raising=False)

    stderr_buf = io.StringIO()
    with patch.object(sys.stderr, "write", stderr_buf.write):
        mod = task.resolve_backend("completely_nonexistent_package_12345")

    assert mod is None
    logs = stderr_buf.getvalue()
    assert "\x01w\x02[Discovery] Backend package 'completely_nonexistent_package_12345' not found" in logs
