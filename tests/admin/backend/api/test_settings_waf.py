"""Tests for the admin Settings WAF endpoints."""

from __future__ import annotations

import pytest

from admin.backend.api.v1.settings import ConfigPatcher, build_settings_response, waf_payload
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


def test_settings_response_defaults_to_disabled_waf() -> None:
    waf = build_settings_response(_config())["waf"]
    assert waf["enabled"] is False and waf["mode"] == "DetectionOnly"
    assert waf["modes"] == ["Off", "DetectionOnly", "On"]
    assert "installed" in waf


def test_patcher_applies_waf() -> None:
    config = _config()
    error = ConfigPatcher(
        config,
        {
            "waf": {
                "enabled": True,
                "mode": "On",
                "paranoia": 3,
                "inbound_threshold": 8,
                "body_limit": "100m",
                "inspect_responses": True,
                "exclusions": ["SecRuleRemoveById 942100", "   "],  # blank dropped
                "exempt_paths": ["/api/method/ping"],
            }
        },
    ).apply()

    assert error is None
    assert config.waf.enabled and config.waf.mode == "On" and config.waf.paranoia == 3
    assert config.waf.exclusions == ["SecRuleRemoveById 942100"]
    assert waf_payload(config)["exempt_paths"] == ["/api/method/ping"]


def test_patcher_rejects_invalid_mode() -> None:
    error = ConfigPatcher(_config(), {"waf": {"mode": "bogus"}}).apply()
    assert error is not None and "waf.mode" in error


@pytest.mark.parametrize("field", ["paranoia", "inbound_threshold"])
def test_patcher_rejects_non_integer_numeric_fields(field: str) -> None:
    # A malformed value must yield a clean error string, never an unhandled 500.
    error = ConfigPatcher(_config(), {"waf": {field: "high"}}).apply()
    assert error is not None and f"waf.{field}" in error


def test_patcher_accepts_numeric_string() -> None:
    # A numeric string still coerces cleanly.
    error = ConfigPatcher(_config(), {"waf": {"paranoia": "3"}}).apply()
    assert error is None


def test_patcher_leaves_waf_untouched_when_absent() -> None:
    config = _config()
    config.waf.enabled = True
    ConfigPatcher(config, {"admin": {"tls": True}}).apply()
    assert config.waf.enabled is True


def test_patcher_applies_custom_rule_and_drops_blank_conditions() -> None:
    config = _config()
    error = ConfigPatcher(
        config,
        {
            "waf": {
                "custom_rules": [
                    {
                        "name": "Block admin abroad",
                        "action": "block",
                        "match": "all",
                        "enabled": True,
                        "conditions": [
                            {"field": "uri_path", "operator": "starts_with", "value": "/admin"},
                            {
                                "field": "",
                                "operator": "",
                                "value": "",
                            },  # trailing blank row dropped
                        ],
                    }
                ]
            }
        },
    ).apply()
    assert error is None
    rule = config.waf.custom_rules[0]
    assert rule.name == "Block admin abroad" and len(rule.conditions) == 1


def test_patcher_rejects_malicious_custom_rule_cleanly() -> None:
    # An injection attempt must yield a clean error string, never an unhandled 500.
    error = ConfigPatcher(
        _config(),
        {
            "waf": {
                "custom_rules": [
                    {
                        "name": "x",
                        "conditions": [{"field": "uri_path", "operator": "is", "value": '/a" "deny"'}],
                    }
                ]
            }
        },
    ).apply()
    assert error is not None and "value" in error


def test_settings_response_exposes_rule_vocabulary() -> None:
    waf = build_settings_response(_config())["waf"]
    assert waf["rule_fields"] and waf["rule_operators"] and waf["rule_actions"]
    assert "block" in waf["rule_actions"] and "uri_path" in waf["rule_fields"]
