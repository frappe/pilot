"""Tests for the admin Settings HTTPS toggle (admin.tls + Let's Encrypt email)."""

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


def test_settings_response_exposes_tls_and_email() -> None:
    config = _config()
    config.admin.tls = True
    config.letsencrypt.email = "ops@example.com"

    payload = build_settings_response(config)

    assert payload["admin"]["tls"] is True
    assert payload["admin"]["domain"] == "admin.example.com"
    assert payload["letsencrypt"]["email"] == "ops@example.com"


def test_patcher_enables_tls_and_sets_email() -> None:
    config = _config()
    assert config.admin.tls is False  # opt-in: off by default

    error = ConfigPatcher(
        config, {"admin": {"tls": True}, "letsencrypt": {"email": "ops@example.com"}}
    ).apply()

    assert error is None
    assert config.admin.tls is True
    assert config.letsencrypt.email == "ops@example.com"


def test_patcher_disables_tls() -> None:
    config = _config()
    config.admin.tls = True

    error = ConfigPatcher(config, {"admin": {"tls": False}}).apply()

    assert error is None
    assert config.admin.tls is False
