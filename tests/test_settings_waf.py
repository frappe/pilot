"""Tests for the admin Settings WAF endpoints."""
from __future__ import annotations

from pilot.config.bench_config import BenchConfig

from admin.backend.views.settings import ConfigPatcher, _build_settings_response, _waf_payload


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
            "admin": {"domain": "admin.example.com"},
        }
    )


def test_settings_response_defaults_to_disabled_waf() -> None:
    waf = _build_settings_response(_config())["waf"]
    assert waf["enabled"] is False and waf["mode"] == "DetectionOnly"
    assert waf["modes"] == ["Off", "DetectionOnly", "On"]
    assert "installed" in waf


def test_patcher_applies_waf() -> None:
    config = _config()
    error = ConfigPatcher(config, {
        "waf": {
            "enabled": True, "mode": "On", "paranoia": 3, "inbound_threshold": 8,
            "body_limit": "100m", "inspect_responses": True,
            "exclusions": ["SecRuleRemoveById 942100", "   "],  # blank dropped
            "exempt_paths": ["/api/method/ping"],
        }
    }).apply()

    assert error is None
    assert config.waf.enabled and config.waf.mode == "On" and config.waf.paranoia == 3
    assert config.waf.exclusions == ["SecRuleRemoveById 942100"]
    assert _waf_payload(config)["exempt_paths"] == ["/api/method/ping"]


def test_patcher_rejects_invalid_mode() -> None:
    error = ConfigPatcher(_config(), {"waf": {"mode": "bogus"}}).apply()
    assert error is not None and "waf.mode" in error


def test_patcher_leaves_waf_untouched_when_absent() -> None:
    config = _config()
    config.waf.enabled = True
    ConfigPatcher(config, {"admin": {"tls": True}}).apply()
    assert config.waf.enabled is True
