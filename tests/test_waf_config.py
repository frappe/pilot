"""Tests for WafConfig parsing, validation, and TOML round-tripping."""
from __future__ import annotations

import tomllib

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.toml_writer import bench_config_to_toml
from pilot.config.waf_config import WAF_MODES, WafConfig, parse_nginx_size
from pilot.exceptions import ConfigError


def _config(waf: dict | None = None, client_max_body_size: str = "50m") -> BenchConfig:
    data: dict = {
        "bench": {"name": "test-bench", "python": "3.14"},
        "nginx": {"client_max_body_size": client_max_body_size},
    }
    if waf is not None:
        data["waf"] = waf
    config = BenchConfig._from_dict(data)
    config.validate()
    return config


@pytest.mark.parametrize("value,expected", [("50m", 50 * 1024**2), ("1g", 1024**3), ("13107200", 13107200), ("512k", 512 * 1024)])
def test_parse_nginx_size(value: str, expected: int) -> None:
    assert parse_nginx_size(value) == expected


def test_defaults_when_section_absent() -> None:
    waf = _config().waf
    assert waf == WafConfig()
    assert waf.enabled is False and waf.mode == "DetectionOnly"


def test_full_section_parses() -> None:
    waf = _config({
        "enabled": True, "mode": "On", "paranoia": 3, "inbound_threshold": 8,
        "body_limit": "100m", "inspect_responses": True,
        "exclusions": ["SecRuleRemoveById 942100"], "exempt_paths": ["/api/method/x"],
    }).waf
    assert waf.enabled and waf.mode == "On" and waf.paranoia == 3
    assert waf.exclusions == ["SecRuleRemoveById 942100"]
    assert waf.exempt_paths == ["/api/method/x"]


@pytest.mark.parametrize("waf,needle", [
    ({"mode": "bogus"}, "waf.mode"),
    ({"paranoia": 7}, "waf.paranoia"),
    ({"paranoia": "high"}, "waf.paranoia"),
    ({"inbound_threshold": 0}, "waf.inbound_threshold"),
    ({"body_limit": "abc"}, "waf.body_limit"),
])
def test_invalid_values_rejected(waf: dict, needle: str) -> None:
    with pytest.raises(ConfigError) as exc:
        _config(waf)
    assert needle in str(exc.value)


def test_body_limit_below_client_max_rejected_when_enabled() -> None:
    with pytest.raises(ConfigError) as exc:
        _config({"enabled": True, "body_limit": "10m"}, client_max_body_size="50m")
    assert "body_limit" in str(exc.value)


def test_body_limit_coupling_skipped_when_disabled() -> None:
    # A small body_limit is tolerated while the WAF is off.
    assert _config({"enabled": False, "body_limit": "10m"}, "50m").waf.body_limit == "10m"


def test_toml_round_trip_preserves_quotes() -> None:
    config = _config()
    tricky = 'SecRuleUpdateTargetById 942100 "!ARGS:filters"'
    config.waf = WafConfig(enabled=True, mode="On", paranoia=4, inbound_threshold=8,
                           body_limit="100m", inspect_responses=True,
                           exclusions=[tricky, "SecRuleRemoveById 949110"],
                           exempt_paths=["/files/"])
    rendered = bench_config_to_toml(config)

    reparsed = BenchConfig._from_dict(tomllib.loads(rendered))
    reparsed.validate()
    assert reparsed.waf == config.waf
    assert reparsed.waf.exclusions[0] == tricky


def test_disabled_waf_writes_no_section() -> None:
    assert "[waf]" not in bench_config_to_toml(_config())


def test_disabled_waf_with_exclusions_is_preserved() -> None:
    config = _config()
    config.waf = WafConfig(enabled=False, exclusions=["SecRuleRemoveById 1"])
    rendered = bench_config_to_toml(config)
    assert "[waf]" in rendered
    assert BenchConfig._from_dict(tomllib.loads(rendered)).waf.exclusions == ["SecRuleRemoveById 1"]


def test_waf_modes_are_the_allowed_set() -> None:
    assert set(WAF_MODES) == {"Off", "DetectionOnly", "On"}


def test_module_package_aliases_per_distro() -> None:
    from pilot.package_managers import AptPackageManager, DnfPackageManager, PacmanPackageManager

    assert AptPackageManager()._resolve("modsecurity-nginx") == ["libnginx-mod-http-modsecurity"]
    assert DnfPackageManager()._resolve("modsecurity-nginx") == ["nginx-mod-modsecurity"]
    assert PacmanPackageManager()._resolve("modsecurity-nginx") == ["nginx-mod-modsecurity"]
