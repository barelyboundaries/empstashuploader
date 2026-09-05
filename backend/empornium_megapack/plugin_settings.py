"""Resolver for Stash plugin settings with strict precedence.

Precedence order:
1. Environment variables (EMPORNIUM_EMPORNIUM_ANNOUNCE_URL, EMPORNIUM_HAMSTER_API_KEY)
2. Local config file via Settings
3. Stash plugin configuration (configuration { plugins } under empornium-megapack)
4. Not set

Import direction:
This module imports both config.py and gql.py. Neither config.py nor gql.py
may import this module.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, NamedTuple

from .config import Settings, get_settings
from .gql import StashClient
from .torrents import TorrentError, validate_announce_url

logger = logging.getLogger(__name__)

PLUGIN_ID = "empornium-megapack"
TTL_SECONDS = 30.0

_cache: dict[str, str] | None = None
_cache_time: float = 0.0
_lock = threading.Lock()

def clear_cache() -> None:
    """Clear the cached plugin settings."""
    global _cache, _cache_time
    with _lock:
        _cache = None
        _cache_time = 0.0


def refresh(stash_client: StashClient | None = None) -> dict[str, str]:
    """Explicitly clear the TTL cache and re-query Stash for plugin settings."""
    clear_cache()
    return get_plugin_settings(stash_client=stash_client, force_refresh=True)


def get_plugin_settings(
    stash_client: StashClient | None = None,
    force_refresh: bool = False,
) -> dict[str, str]:
    """Query Stash configuration { plugins } through StashClient.

    Returns {"announceUrl": str, "hamsterApiKey": str} with missing fields as "".
    Fails soft: returns {} on any network error, HTTP non-200, or malformed body.
    """
    global _cache, _cache_time

    now = time.monotonic()
    with _lock:
        if not force_refresh and _cache is not None and (now - _cache_time) < TTL_SECONDS:
            return dict(_cache)

    try:
        client = stash_client or StashClient()
        plugins = client.plugin_configuration()
        if not isinstance(plugins, dict):
            return {}
        pkg_settings = plugins.get(PLUGIN_ID)
        if not isinstance(pkg_settings, dict):
            pkg_settings = {}

        result = {
            "announceUrl": str(pkg_settings.get("announceUrl") or "").strip(),
            "hamsterApiKey": str(pkg_settings.get("hamsterApiKey") or "").strip(),
        }

        with _lock:
            _cache = result
            _cache_time = now

        return dict(result)
    except Exception as exc:
        logger.debug("Failed to query Stash plugin configuration: %s", exc)
        return {}


def resolve_announce_url(
    settings: Settings | None = None,
    plugin_settings: dict[str, str] | None = None,
    stash_client: StashClient | None = None,
) -> tuple[str, str]:
    """Resolve the Empornium announce URL following strict precedence:

    1. env var: EMPORNIUM_EMPORNIUM_ANNOUNCE_URL (the doubled prefix is not a
       typo: the field itself is named empornium_announce_url and Settings
       applies the EMPORNIUM_ prefix on top of it)
    2. config file: settings.empornium_announce_url
    3. Stash plugin settings: announceUrl
    4. not set

    Returns (value, source) where source is one of:
    "env", "config file", "Stash plugin settings", "not set".
    """
    env_val = os.environ.get("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL")
    if env_val is not None and env_val.strip():
        return env_val.strip(), "env"

    s = settings or get_settings()
    if s.empornium_announce_url and s.empornium_announce_url.strip():
        return s.empornium_announce_url.strip(), "config file"

    ps = plugin_settings if plugin_settings is not None else get_plugin_settings(stash_client=stash_client)
    stash_val = ps.get("announceUrl", "")
    if stash_val and stash_val.strip():
        return stash_val.strip(), "Stash plugin settings"

    return "", "not set"


def resolve_hamster_api_key(
    settings: Settings | None = None,
    plugin_settings: dict[str, str] | None = None,
    stash_client: StashClient | None = None,
) -> tuple[str, str]:
    """Resolve the HamsterImg API key following strict precedence:

    1. env var: EMPORNIUM_HAMSTER_API_KEY
    2. config file: settings.hamster_api_key
    3. Stash plugin settings: hamsterApiKey
    4. not set

    Returns (value, source) where source is one of:
    "env", "config file", "Stash plugin settings", "not set".
    """
    env_val = os.environ.get("EMPORNIUM_HAMSTER_API_KEY")
    if env_val is not None and env_val.strip():
        return env_val.strip(), "env"

    s = settings or get_settings()
    if s.hamster_api_key and s.hamster_api_key.strip():
        return s.hamster_api_key.strip(), "config file"

    ps = plugin_settings if plugin_settings is not None else get_plugin_settings(stash_client=stash_client)
    stash_val = ps.get("hamsterApiKey", "")
    if stash_val and stash_val.strip():
        return stash_val.strip(), "Stash plugin settings"

    return "", "not set"


class AnnounceValidity(NamedTuple):
    valid: bool
    reason: str

    @property
    def is_valid(self) -> bool:
        return self.valid


def announce_validity(
    settings: Settings | str | None = None,
    plugin_settings: dict[str, str] | None = None,
    stash_client: StashClient | None = None,
    *,
    announce_url: str | None = None,
) -> AnnounceValidity:
    """Resolve the Empornium announce URL and check its validity.

    Returns an AnnounceValidity(valid, reason) tuple:
    - If valid: (True, "")
    - If unset: (False, "not configured")
    - If configured but invalid: (False, <generic human-readable reason>)

    The reason NEVER contains or interpolates the URL itself.
    """
    if isinstance(settings, str):
        url = settings.strip()
    elif announce_url is not None:
        url = announce_url.strip()
    else:
        url, _ = resolve_announce_url(
            settings=settings,
            plugin_settings=plugin_settings,
            stash_client=stash_client,
        )

    if not url:
        return AnnounceValidity(False, "not configured")

    try:
        validate_announce_url(url)
        return AnnounceValidity(True, "")
    except TorrentError as err:
        return AnnounceValidity(False, str(err))
    except Exception:
        return AnnounceValidity(False, "Invalid announce URL.")

