"""Tests for editing the [llm] config via the admin Settings API."""

from __future__ import annotations

from admin.backend.api.v1.settings import ConfigPatcher
from pilot.config import BenchConfig


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
        }
    )


def test_patcher_updates_provider_and_key() -> None:
    config = _config()

    error = ConfigPatcher(
        config, {"llm": {"provider": "anthropic", "api_key": "sk-key", "max_tokens": 2048}}
    ).apply()

    assert error is None
    assert config.llm.provider == "anthropic"
    assert config.llm.api_key == "sk-key"
    assert config.llm.max_tokens == 2048


def test_patcher_sets_key_only_when_provided() -> None:
    config = _config()
    config.llm.api_key = "original"

    # blank key is preserved, not cleared (write-only field)
    ConfigPatcher(config, {"llm": {"provider": "openai", "api_key": ""}}).apply()
    assert config.llm.api_key == "original"
    assert config.llm.provider == "openai"


def test_patcher_ignores_absent_llm_section() -> None:
    config = _config()
    assert ConfigPatcher(config, {}).apply() is None
    assert config.llm == type(config.llm)()
