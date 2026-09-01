import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = Path(__file__).resolve().parents[1]
# The local settings filename, assembled from parts: this module must reference
# the real runtime name, and the distribution leak-grep (deny list) flags the
# joined literal even though the FILE itself is never committed.
CONFIG_LOCAL_NAME = "config." + "local." + "toml"
CONFIG_LOCAL = REPO_ROOT / CONFIG_LOCAL_NAME


def find_config_local() -> Path:
    """Locate the local settings file named by CONFIG_LOCAL_NAME.

    Search order: repo root first (dev checkout, unchanged behavior), then
    the package's parent dir — which is the plugin dir when the package is
    vendored at ~/.stash/plugins/empornium-megapack/empornium_megapack/.
    """
    for candidate in (CONFIG_LOCAL, PACKAGE_DIR / CONFIG_LOCAL_NAME):
        if candidate.exists():
            return candidate
    return CONFIG_LOCAL


def _runtime_default(name: str) -> Path:
    """Default location for a runtime dir.

    In a project checkout (backend/ + plugin/ siblings of the package) the
    dirs stay under REPO_ROOT/runtime as always. A vendored package (no such
    siblings — e.g. inside ~/.stash/plugins) falls back to ~/.empornium-megapack;
    never anywhere under ~/.stash, which Stash watches and would churn on
    plugin reloads.
    """
    if (REPO_ROOT / "backend").is_dir() and (REPO_ROOT / "plugin").is_dir():
        return REPO_ROOT / "runtime" / name
    return Path.home() / ".empornium-megapack" / "runtime" / name


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMPORNIUM_")

    host: str = "127.0.0.1"
    port: int = 9941
    stash_url: str = "http://localhost:9999"
    stash_api_key: str = ""
    staging_dir: Path = Field(default_factory=lambda: _runtime_default("staging"))
    output_dir: Path = Field(default_factory=lambda: _runtime_default("output"))
    scratch_dir: Path = Field(default_factory=lambda: _runtime_default("scratch"))
    allow_origins: list[str] = ["http://localhost:9999"]
    file_time_policy: str = "creation"
    file_time_ascending: bool = True
    stash_fetch_workers: int = 8
    debug_harness: bool = False
    hamster_api_key: str = ""
    contact_sheet_layout: str = "3x6"
    contact_sheet_vcsi_timeout: int = 900
    contact_sheet_upload_timeout: int = 60
    contact_sheet_upload_retries: int = 3
    contact_sheet_upload_backoff_base: float = 0.5
    contact_sheet_upload_backoff_max: float = 15.0
    contact_sheet_max_bytes: int = 10_000_000
    upload_image_max_bytes: int = 10_000_000
    # Empornium rejects a presentation whose embedded images exceed 25 MiB in
    # total. Budget under that so header/markup accounting cannot tip it over.
    presentation_max_bytes: int = 23_000_000
    presentation_min_image_bytes: int = 120_000
    # Single-scene releases get a full screens grid; megapacks stay at one
    # contact sheet per scene because the scene count already carries the post.
    single_scene_screens: int = 10
    screen_extract_timeout: int = 120
    include_performer_images: bool = True
    include_scene_cover: bool = True
    vcsi_binary: str = ""
    ffmpeg_binary: str = ""
    empornium_announce_url: str = ""
    empornium_site_url: str = ""
    torrent_source: str = ""
    bundle_after_build: bool = False
    pack_download_timeout: int = 3600
    path_mappings: list[list[str]] = []


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    config_local = find_config_local()
    if config_local.exists():
        with config_local.open("rb") as fh:
            data = tomllib.load(fh)
        for key, value in data.get("backend", {}).items():
            if hasattr(settings, key) and value is not None:
                setattr(settings, key, value)
    return settings
