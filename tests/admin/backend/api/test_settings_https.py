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
    assert payload["admin"]["public_tls"] is True
    assert payload["admin"]["public_scheme"] == "https"
    assert payload["admin"]["domain"] == "admin.example.com"
    assert payload["letsencrypt"]["email"] == "ops@example.com"


def test_patcher_sets_letsencrypt_email() -> None:
    config = _config()

    assert ConfigPatcher(config, {"letsencrypt": {"email": " ops@example.com "}}).apply() is None
    assert config.letsencrypt.email == "ops@example.com"


def test_patcher_rejects_invalid_letsencrypt_email() -> None:
    config = _config()

    error = ConfigPatcher(config, {"letsencrypt": {"email": "not-an-email"}}).apply()

    assert error == "letsencrypt.email 'not-an-email' is not a valid email address."


def test_patcher_leaves_letsencrypt_email_alone_when_unmentioned() -> None:
    config = _config()
    config.letsencrypt.email = "ops@example.com"

    ConfigPatcher(config, {"bench": {"http_port": 8001}}).apply()

    assert config.letsencrypt.email == "ops@example.com"
