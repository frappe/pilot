"""Tests for the admin Settings update channel."""

from __future__ import annotations

from admin.backend.api.v1.settings import ConfigPatcher
from pilot.config import BenchConfig


def _config() -> BenchConfig:
    return BenchConfig._from_dict({"bench": {"name": "test-bench", "python": "3.14"}})


def test_patcher_sets_the_update_channel() -> None:
    config = _config()

    assert ConfigPatcher(config, {"central": {"update_channel": "early"}}).apply() is None
    assert config.central.update_channel == "early"


def test_patcher_refuses_an_unknown_update_channel() -> None:
    error = ConfigPatcher(_config(), {"central": {"update_channel": "Early"}}).apply()

    assert "must be one of early, normal, late" in error
