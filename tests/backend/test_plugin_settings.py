import os
from unittest.mock import MagicMock
import pytest

from empornium_megapack.config import Settings
from empornium_megapack.gql import StashError
from empornium_megapack.plugin_settings import (
    clear_cache,
    get_plugin_settings,
    refresh,
    resolve_announce_url,
    resolve_hamster_api_key,
)


@pytest.fixture(autouse=True)
def _clean_cache_and_env(monkeypatch):
    clear_cache()
    monkeypatch.delenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", raising=False)
    monkeypatch.delenv("EMPORNIUM_HAMSTER_API_KEY", raising=False)
    yield
    clear_cache()


def test_announce_url_precedence_env_beats_toml_beats_stash(monkeypatch):
    mock_client = MagicMock()
    mock_client.plugin_configuration.return_value = {
        "empornium-megapack": {
            "announceUrl": "http://tracker.stash.example:2710/stash/announce",
        }
    }

    settings = Settings(empornium_announce_url="http://tracker.toml.example:2710/toml/announce")
    monkeypatch.setenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", "http://tracker.env.example:2710/env/announce")

    # 1. Env beats both toml and Stash
    val, source = resolve_announce_url(settings=settings, stash_client=mock_client)
    assert val == "http://tracker.env.example:2710/env/announce"
    assert source == "env"

    # 2. Toml beats Stash when env is absent
    monkeypatch.delenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", raising=False)
    val, source = resolve_announce_url(settings=settings, stash_client=mock_client)
    assert val == "http://tracker.toml.example:2710/toml/announce"
    assert source == "config file"

    # 3. Stash fills in when neither env nor toml is set
    empty_settings = Settings(empornium_announce_url="")
    clear_cache()
    val, source = resolve_announce_url(settings=empty_settings, stash_client=mock_client)
    assert val == "http://tracker.stash.example:2710/stash/announce"
    assert source == "Stash plugin settings"

    # 4. Nothing set anywhere returns empty and "not set"
    clear_cache()
    empty_client = MagicMock()
    empty_client.plugin_configuration.return_value = {}
    val, source = resolve_announce_url(settings=empty_settings, stash_client=empty_client)
    assert val == ""
    assert source == "not set"


def test_hamster_api_key_precedence_env_beats_toml_beats_stash(monkeypatch):
    mock_client = MagicMock()
    mock_client.plugin_configuration.return_value = {
        "empornium-megapack": {
            "hamsterApiKey": "stash_key_123",
        }
    }

    settings = Settings(hamster_api_key="toml_key_456")
    monkeypatch.setenv("EMPORNIUM_HAMSTER_API_KEY", "env_key_789")

    # 1. Env beats both toml and Stash
    val, source = resolve_hamster_api_key(settings=settings, stash_client=mock_client)
    assert val == "env_key_789"
    assert source == "env"

    # 2. Toml beats Stash when env is absent
    monkeypatch.delenv("EMPORNIUM_HAMSTER_API_KEY", raising=False)
    val, source = resolve_hamster_api_key(settings=settings, stash_client=mock_client)
    assert val == "toml_key_456"
    assert source == "config file"

    # 3. Stash fills in when neither env nor toml is set
    empty_settings = Settings(hamster_api_key="")
    clear_cache()
    val, source = resolve_hamster_api_key(settings=empty_settings, stash_client=mock_client)
    assert val == "stash_key_123"
    assert source == "Stash plugin settings"

    # 4. Nothing set anywhere returns empty and "not set"
    clear_cache()
    empty_client = MagicMock()
    empty_client.plugin_configuration.return_value = {}
    val, source = resolve_hamster_api_key(settings=empty_settings, stash_client=empty_client)
    assert val == ""
    assert source == "not set"


def test_stash_unreachable_fails_soft():
    error_client = MagicMock()
    error_client.plugin_configuration.side_effect = StashError("Connection refused")

    # get_plugin_settings must return {} and not raise
    res = get_plugin_settings(stash_client=error_client, force_refresh=True)
    assert res == {}

    empty_settings = Settings(empornium_announce_url="", hamster_api_key="")
    val, source = resolve_announce_url(settings=empty_settings, stash_client=error_client)
    assert val == ""
    assert source == "not set"

    val, source = resolve_hamster_api_key(settings=empty_settings, stash_client=error_client)
    assert val == ""
    assert source == "not set"


def test_stash_malformed_response_fails_soft():
    malformed_client = MagicMock()
    malformed_client.plugin_configuration.return_value = None

    res = get_plugin_settings(stash_client=malformed_client, force_refresh=True)
    assert res == {}

    empty_settings = Settings(empornium_announce_url="", hamster_api_key="")
    val, source = resolve_announce_url(settings=empty_settings, stash_client=malformed_client)
    assert val == ""
    assert source == "not set"


def test_ttl_cache_and_refresh():
    mock_client = MagicMock()
    mock_client.plugin_configuration.return_value = {
        "empornium-megapack": {
            "announceUrl": "http://tracker.one.example/announce",
            "hamsterApiKey": "key_one",
        }
    }

    res1 = get_plugin_settings(stash_client=mock_client, force_refresh=True)
    assert res1["announceUrl"] == "http://tracker.one.example/announce"
    assert mock_client.plugin_configuration.call_count == 1

    # Second call uses cache
    res2 = get_plugin_settings(stash_client=mock_client)
    assert res2 == res1
    assert mock_client.plugin_configuration.call_count == 1

    # Update mock return
    mock_client.plugin_configuration.return_value = {
        "empornium-megapack": {
            "announceUrl": "http://tracker.two.example/announce",
            "hamsterApiKey": "key_two",
        }
    }

    # Explicit refresh clears cache and refetches
    res3 = refresh(stash_client=mock_client)
    assert res3["announceUrl"] == "http://tracker.two.example/announce"
    assert mock_client.plugin_configuration.call_count == 2


def test_single_prefix_announce_env_var_is_not_honored(monkeypatch):
    """Only EMPORNIUM_EMPORNIUM_ANNOUNCE_URL is read.

    The doubled prefix is what pydantic's Settings binds to (env_prefix
    EMPORNIUM_ + field empornium_announce_url). Honoring a single-prefix
    EMPORNIUM_ANNOUNCE_URL here would make the resolver disagree with every
    other reader of settings.empornium_announce_url.
    """
    monkeypatch.delenv("EMPORNIUM_EMPORNIUM_ANNOUNCE_URL", raising=False)
    monkeypatch.setenv("EMPORNIUM_ANNOUNCE_URL", "http://tracker.fallback.example:2710/fallback/announce")
    empty_settings = Settings(empornium_announce_url="")
    empty_client = MagicMock()
    empty_client.plugin_configuration.return_value = {}
    val, source = resolve_announce_url(settings=empty_settings, stash_client=empty_client)
    assert val == ""
    assert source == "not set"


def test_plugin_configuration_unwraps_and_tolerates_bad_envelopes(monkeypatch):
    """StashClient.plugin_configuration owns the GraphQL envelope shape.

    plugin_settings now consumes the unwrapped plugins map, so the envelope
    handling has to be verified here rather than through the resolver.
    """
    from empornium_megapack.gql import StashClient

    client = StashClient(settings=Settings())
    plugins = {"empornium-megapack": {"announceUrl": "http://t.example/announce"}}

    monkeypatch.setattr(client, "_post", lambda q, v: {"configuration": {"plugins": plugins}})
    assert client.plugin_configuration() == plugins

    for bad in ({}, {"configuration": None}, {"configuration": {}}, {"configuration": {"plugins": None}}):
        monkeypatch.setattr(client, "_post", lambda q, v, b=bad: b)
        assert client.plugin_configuration() == {}
