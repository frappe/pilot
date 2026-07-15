"""Tests for the admin Settings firewall (allow/block list) endpoints."""
from __future__ import annotations

from pilot.config.bench_config import BenchConfig

from admin.backend.views.settings import ConfigPatcher, _build_settings_response, _firewall_payload


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
            "admin": {"domain": "admin.example.com"},
        }
    )


def test_settings_response_defaults_to_open_firewall() -> None:
    payload = _build_settings_response(_config())
    assert payload["firewall"] == {"enabled": False, "default": "allow", "rules": []}


def test_patcher_applies_firewall_rules() -> None:
    config = _config()
    error = ConfigPatcher(config, {
        "firewall": {
            "enabled": True,
            "default": "deny",
            "rules": [
                {"ip": "203.0.113.4", "action": "allow", "description": "office"},
                {"ip": "", "action": "deny"},  # blank IP is dropped
            ],
        }
    }).apply()

    assert error is None
    assert config.firewall.enabled is True
    assert config.firewall.default == "deny"
    assert len(config.firewall.rules) == 1
    assert config.firewall.rules[0].ip == "203.0.113.4"
    assert _firewall_payload(config)["rules"][0]["description"] == "office"


def test_patcher_rejects_invalid_ip() -> None:
    config = _config()
    error = ConfigPatcher(config, {"firewall": {"enabled": True, "rules": [{"ip": "nope", "action": "deny"}]}}).apply()
    assert error is not None and "nope" in error


def test_patcher_leaves_firewall_untouched_when_absent() -> None:
    config = _config()
    config.firewall.enabled = True
    ConfigPatcher(config, {"admin": {"tls": True}}).apply()
    assert config.firewall.enabled is True


def test_my_ip_route_ignores_x_real_ip_from_untrusted_peer() -> None:
    from admin.backend.views.settings import network_bp
    from flask import Flask

    app = Flask(__name__)
    app.config["TRUSTED_PROXY_PEERS"] = ()
    app.register_blueprint(network_bp, url_prefix="/api/v1")
    client = app.test_client()

    resp = client.get("/api/v1/network/client", headers={"X-Real-IP": "203.0.113.9"})
    assert resp.get_json() == {"ip": "127.0.0.1"}


def test_my_ip_route_reads_x_real_ip_from_trusted_peer() -> None:
    from admin.backend.views.settings import network_bp
    from flask import Flask

    app = Flask(__name__)
    app.config["TRUSTED_PROXY_PEERS"] = ("127.0.0.1",)
    app.register_blueprint(network_bp, url_prefix="/api/v1")
    client = app.test_client()

    resp = client.get("/api/v1/network/client", headers={"X-Real-IP": "203.0.113.9"})
    assert resp.get_json() == {"ip": "203.0.113.9"}
