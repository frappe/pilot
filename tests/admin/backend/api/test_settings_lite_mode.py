"""Tests for the admin Settings lite mode switch."""

from __future__ import annotations

from admin.backend.api.v1.settings import ConfigPatcher, build_settings_response
from pilot.config import BenchConfig
from pilot.core.bench.settings import BenchSettings
from pilot.core.bench.settings.payload import SettingsPayload


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
            "admin": {"domain": "admin.example.com"},
        }
    )


def test_settings_response_reports_lite_mode_off() -> None:
    assert build_settings_response(_config())["lite_mode"] == {"enabled": False, "supported": False}


def test_patcher_toggles_lite_mode() -> None:
    config = _config()

    assert ConfigPatcher(config, {"lite_mode": {"enabled": True}}).apply() is None
    assert config.lite_mode.enabled is True

    assert ConfigPatcher(config, {"lite_mode": {"enabled": False}}).apply() is None
    assert config.lite_mode.enabled is False


def test_patcher_leaves_lite_mode_alone_when_unmentioned() -> None:
    config = _config()
    config.lite_mode.enabled = True

    ConfigPatcher(config, {"bench": {"http_port": 8001}}).apply()

    assert config.lite_mode.enabled is True


def test_toggling_lite_mode_triggers_a_process_rebuild() -> None:
    config = _config()
    before = SettingsPayload(config).restart_triggers
    config.lite_mode.enabled = True

    assert BenchSettings.is_restart_needed(before, SettingsPayload(config).restart_triggers)
