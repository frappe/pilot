from __future__ import annotations

from admin.backend.api.v1.settings import ConfigPatcher, build_settings_response, restart_trigger_values
from pilot.config import BenchConfig
from pilot.core.bench.settings import is_restart_needed


def _config(**bench) -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14", **bench},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
        }
    )


def test_settings_response_shows_onboarding_on_a_new_bench() -> None:
    assert build_settings_response(_config())["bench"]["onboarding"] == ""


def test_patcher_stores_onboarding_state() -> None:
    config = _config()

    assert ConfigPatcher(config, {"bench": {"onboarding": "minimized"}}).apply() is None
    assert config.onboarding == "minimized"

    assert ConfigPatcher(config, {"bench": {"onboarding": "dismissed"}}).apply() is None
    assert config.onboarding == "dismissed"


def test_patcher_rejects_unknown_onboarding_state() -> None:
    config = _config()

    error = ConfigPatcher(config, {"bench": {"onboarding": "closed"}}).apply()

    assert error and "bench.onboarding" in error
    assert config.onboarding == ""


def test_onboarding_state_survives_a_round_trip() -> None:
    config = _config(onboarding="dismissed")

    assert BenchConfig._from_dict(config._to_toml_dict()).onboarding == "dismissed"


def test_new_bench_does_not_write_onboarding_key() -> None:
    assert "onboarding" not in _config()._to_toml_dict()["bench"]


def test_changing_onboarding_does_not_restart_the_bench() -> None:
    config = _config()
    before = restart_trigger_values(config)
    config.onboarding = "minimized"

    assert not is_restart_needed(before, restart_trigger_values(config))
