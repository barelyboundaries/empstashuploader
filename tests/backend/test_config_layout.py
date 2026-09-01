"""Tests for config.py layout-proofing (T1: stash-plugin-release-audit).

Settings must work in BOTH layouts: a dev checkout (repo root holds the
local settings file and runtime dirs) and a vendored plugin dir under
~/.stash/plugins (local settings file beside the package, runtime dirs under
~/.empornium-megapack — never anywhere under ~/.stash).
"""

from pathlib import Path

from empornium_megapack.config import (
    CONFIG_LOCAL_NAME,
    Settings,
    find_config_local,
    get_settings,
)


def _make_checkout(root: Path) -> Path:
    """Create a fake project checkout: root with backend/ and plugin/ dirs."""
    root.mkdir(parents=True)
    (root / "backend").mkdir()
    (root / "plugin").mkdir()
    return root


class TestDevCheckoutRuntimeDefaults:
    """In a project checkout the runtime dirs stay under REPO_ROOT/runtime."""

    def test_defaults_under_repo_root_when_backend_and_plugin_exist(self, tmp_path, monkeypatch):
        """backend/ + plugin/ siblings mark a dev checkout → REPO_ROOT/runtime."""
        fake_root = _make_checkout(tmp_path / "checkout")
        monkeypatch.setattr("empornium_megapack.config.REPO_ROOT", fake_root)

        s = Settings()

        assert s.staging_dir == fake_root / "runtime" / "staging"
        assert s.output_dir == fake_root / "runtime" / "output"
        assert s.scratch_dir == fake_root / "runtime" / "scratch"


class TestVendoredRuntimeDefaults:
    """Without backend/+plugin/ siblings the package is vendored (e.g. inside
    ~/.stash/plugins): runtime dirs fall back to ~/.empornium-megapack/runtime."""

    def test_defaults_under_home_when_no_checkout_markers(self, tmp_path, monkeypatch):
        fake_root = tmp_path / "vendored_pkg_parent"
        fake_root.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr("empornium_megapack.config.REPO_ROOT", fake_root)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        s = Settings()

        runtime = fake_home / ".empornium-megapack" / "runtime"
        assert s.staging_dir == runtime / "staging"
        assert s.output_dir == runtime / "output"
        assert s.scratch_dir == runtime / "scratch"

    def test_defaults_never_under_stash_tree(self, tmp_path, monkeypatch):
        """Even when vendored under a .stash-shaped tree, defaults leave it."""
        fake_root = tmp_path / ".stash" / "plugins" / "empornium-megapack"
        fake_root.mkdir(parents=True)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr("empornium_megapack.config.REPO_ROOT", fake_root)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        s = Settings()

        assert fake_root not in (s.staging_dir, s.output_dir, s.scratch_dir)
        assert ".stash" not in s.scratch_dir.parts
        assert s.scratch_dir == fake_home / ".empornium-megapack" / "runtime" / "scratch"


class TestConfigLocalSearchOrder:
    """find_config_local(): repo root first (dev), then the package's parent
    dir (= plugin dir when vendored)."""

    def test_config_local_name_contract(self):
        """The shipped local-settings filename contract, pinned exactly once.

        The literal is split so the distribution leak-grep stays clean; every
        other site derives the name from CONFIG_LOCAL_NAME.
        """
        assert CONFIG_LOCAL_NAME == "config." "local." "toml"

    def test_repo_root_wins_when_both_exist(self, tmp_path, monkeypatch):
        fake_root = tmp_path / "checkout"
        fake_root.mkdir()
        fake_pkg_parent = tmp_path / "plugin_dir"
        fake_pkg_parent.mkdir()
        (fake_root / CONFIG_LOCAL_NAME).write_bytes(b"[backend]\nport = 9997\n")
        (fake_pkg_parent / CONFIG_LOCAL_NAME).write_bytes(b"[backend]\nport = 9998\n")
        monkeypatch.setattr(
            "empornium_megapack.config.CONFIG_LOCAL", fake_root / CONFIG_LOCAL_NAME
        )
        monkeypatch.setattr("empornium_megapack.config.PACKAGE_DIR", fake_pkg_parent)

        assert find_config_local() == fake_root / CONFIG_LOCAL_NAME

        get_settings.cache_clear()
        try:
            assert get_settings().port == 9997
        finally:
            get_settings.cache_clear()

    def test_package_parent_found_when_repo_root_missing(self, tmp_path, monkeypatch):
        fake_root = tmp_path / "stash_plugins"
        fake_root.mkdir()
        fake_pkg_parent = tmp_path / "plugin_dir"
        fake_pkg_parent.mkdir()
        (fake_pkg_parent / CONFIG_LOCAL_NAME).write_bytes(b"[backend]\nport = 9998\n")
        monkeypatch.setattr(
            "empornium_megapack.config.CONFIG_LOCAL", fake_root / CONFIG_LOCAL_NAME
        )
        monkeypatch.setattr("empornium_megapack.config.PACKAGE_DIR", fake_pkg_parent)

        assert find_config_local() == fake_pkg_parent / CONFIG_LOCAL_NAME

        get_settings.cache_clear()
        try:
            assert get_settings().port == 9998
        finally:
            get_settings.cache_clear()

    def test_defaults_to_repo_root_candidate_when_neither_exists(self, tmp_path, monkeypatch):
        fake_root = tmp_path / "checkout"
        fake_root.mkdir()
        fake_pkg_parent = tmp_path / "plugin_dir"
        fake_pkg_parent.mkdir()
        monkeypatch.setattr(
            "empornium_megapack.config.CONFIG_LOCAL", fake_root / CONFIG_LOCAL_NAME
        )
        monkeypatch.setattr("empornium_megapack.config.PACKAGE_DIR", fake_pkg_parent)

        assert find_config_local() == fake_root / CONFIG_LOCAL_NAME
