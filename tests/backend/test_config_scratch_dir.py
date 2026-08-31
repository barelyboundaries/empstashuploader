"""Tests for the scratch_dir Settings field (T1: staged-wizard-inplace-seed)."""

from pathlib import Path

from deepseek_megapack.config import CONFIG_LOCAL_NAME, REPO_ROOT, Settings, get_settings


class TestScratchDirDefault:
    """Settings defaults for scratch_dir."""

    def test_default_value(self):
        """scratch_dir defaults to REPO_ROOT / 'runtime' / 'scratch'."""
        s = Settings()
        assert s.scratch_dir == REPO_ROOT / "runtime" / "scratch"

    def test_default_is_path(self):
        """scratch_dir is always a Path instance."""
        s = Settings()
        assert isinstance(s.scratch_dir, Path)


class TestScratchDirTomlOverride:
    """Local settings file [backend] scratch_dir override.

    get_settings() uses raw setattr() — TOML strings stay strings, not Path.
    """

    def test_toml_override_wins(self, tmp_path, monkeypatch):
        """A string value in the local settings [backend] overrides the default."""
        custom = tmp_path / "custom_scratch"
        toml_content = f"""
        [backend]
        scratch_dir = "{custom.as_posix()}"
        """.encode()
        toml_file = tmp_path / CONFIG_LOCAL_NAME
        toml_file.write_bytes(toml_content)

        monkeypatch.setattr("deepseek_megapack.config.CONFIG_LOCAL", toml_file)
        get_settings.cache_clear()
        try:
            s = get_settings()
            # TOML loader does raw setattr — string stays string, not Path
            assert s.scratch_dir == custom.as_posix()
        finally:
            get_settings.cache_clear()

    def test_env_override_wins(self, tmp_path, monkeypatch):
        """DEEPSEEK_SCRATCH_DIR env var overrides the default (via pydantic coercion to Path)."""
        custom = tmp_path / "env_scratch"
        monkeypatch.setenv("DEEPSEEK_SCRATCH_DIR", str(custom))
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.scratch_dir == custom
        finally:
            get_settings.cache_clear()


class TestScratchDirTypeRejection:
    """Type behavior for scratch_dir through the TOML override path."""

    def test_bool_from_toml_is_set_as_bool(self, tmp_path, monkeypatch):
        """A TOML boolean is set raw (setattr), not coerced to Path — documents actual behavior."""
        toml_content = b"""
        [backend]
        scratch_dir = true
        """
        toml_file = tmp_path / CONFIG_LOCAL_NAME
        toml_file.write_bytes(toml_content)

        monkeypatch.setattr("deepseek_megapack.config.CONFIG_LOCAL", toml_file)
        get_settings.cache_clear()
        try:
            s = get_settings()
            # Raw setattr preserves TOML type — bool, not Path
            assert s.scratch_dir is True
        finally:
            get_settings.cache_clear()

    def test_empty_string_overrides_default(self, tmp_path, monkeypatch):
        """An empty string in TOML is not None, so it overrides the default."""
        toml_content = b"""
        [backend]
        scratch_dir = ""
        """
        toml_file = tmp_path / CONFIG_LOCAL_NAME
        toml_file.write_bytes(toml_content)

        monkeypatch.setattr("deepseek_megapack.config.CONFIG_LOCAL", toml_file)
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.scratch_dir == ""
        finally:
            get_settings.cache_clear()
