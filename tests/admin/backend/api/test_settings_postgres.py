"""Tests for editing the [postgres] connection on the admin Settings page."""

from __future__ import annotations

from admin.backend.api.v1.settings import build_settings_response
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
