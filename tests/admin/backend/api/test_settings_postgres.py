"""Tests for editing the [postgres] connection on the admin Settings page."""

from __future__ import annotations

from admin.backend.api.v1.settings import ConfigPatcher, build_settings_response
from pilot.config import BenchConfig


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
            "admin": {"domain": "admin.example.com"},
        }
    )


def test_response_hides_password_but_flags_when_set() -> None:
    config = _config()
    config.postgres.root_password = "secret"

    payload = build_settings_response(config)

    assert payload["postgres"]["admin_user"] == "postgres"
    assert payload["postgres"]["password_set"] is True
    assert "root_password" not in payload["postgres"]


def test_patcher_updates_connection_fields() -> None:
    config = _config()

    error = ConfigPatcher(
        config, {"postgres": {"host": "db.internal", "port": 5433, "admin_user": "pgroot"}}
    ).apply()

    assert error is None
    assert config.postgres.host == "db.internal"
    assert config.postgres.port == 5433
    assert config.postgres.admin_user == "pgroot"


def test_patcher_sets_password_only_when_provided() -> None:
    config = _config()
    config.postgres.root_password = "original"

    # blank password is preserved, not cleared (write-only field)
    ConfigPatcher(config, {"postgres": {"root_password": ""}}).apply()
    assert config.postgres.root_password == "original"

    ConfigPatcher(config, {"postgres": {"root_password": "changed"}}).apply()
    assert config.postgres.root_password == "changed"
