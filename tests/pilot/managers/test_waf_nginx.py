"""Tests for ModSecurity (WAF) directive rendering and rule-file generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config.bench import BenchConfig
from pilot.config.site import SiteConfig
from pilot.config.waf import WafCondition, WafConfig, WafRule
from pilot.core.bench import Bench
from pilot.managers import nginx
from pilot.managers.nginx import NginxConfigRenderer, NginxManager


def _compile(rules) -> str:
    return NginxConfigRenderer._render_modsec_custom_rules(WafConfig(custom_rules=rules))


def _cond(field, operator, value, header_name=""):
    return WafCondition(field=field, operator=operator, value=value, header_name=header_name)

_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "admin": {"domain": "admin.example.com", "tls": False},
}
_SITE = SiteConfig(name="site1.example.com", apps=["frappe"])


@pytest.fixture
def installed(monkeypatch):
    """Pretend the ModSecurity module + CRS are installed so the install-gated
    render emits directives."""
    monkeypatch.setattr(nginx.WafManager, "is_installed", staticmethod(lambda: True))


def _bench(tmp_path: Path, waf: WafConfig) -> Bench:
    config = BenchConfig._from_dict(_DATA)
    config.waf = waf
    return Bench(config, tmp_path)


def _manager(tmp_path: Path, waf: WafConfig) -> NginxManager:
    return NginxManager(_bench(tmp_path, waf))


def _renderer(tmp_path: Path, waf: WafConfig) -> NginxConfigRenderer:
    return NginxConfigRenderer(_bench(tmp_path, waf))


def test_render_waf_empty_when_disabled(tmp_path: Path, installed) -> None:
    assert _renderer(tmp_path, WafConfig(enabled=False))._render_waf() == ""


def test_render_waf_empty_when_not_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(nginx.WafManager, "is_installed", staticmethod(lambda: False))
    assert _renderer(tmp_path, WafConfig(enabled=True))._render_waf() == ""


def test_render_waf_emits_directives_when_active(tmp_path: Path, installed) -> None:
    out = _renderer(tmp_path, WafConfig(enabled=True))._render_waf()
    assert "modsecurity on;" in out
    assert "modsecurity_rules_file" in out and "modsecurity/main.conf" in out


def test_site_and_admin_vhosts_carry_waf(tmp_path: Path, installed) -> None:
    renderer = _renderer(tmp_path, WafConfig(enabled=True))
    site = renderer._render_http_only_block(_SITE, "test-bench", renderer.bench.config.nginx, renderer.bench.path)
    admin = renderer.generate_admin_config(ssl_ready=False)
    assert "modsecurity on;" in site
    assert "modsecurity on;" in admin


def test_vhosts_clean_when_disabled(tmp_path: Path, installed) -> None:
    renderer = _renderer(tmp_path, WafConfig(enabled=False))
    site = renderer._render_http_only_block(_SITE, "test-bench", renderer.bench.config.nginx, renderer.bench.path)
    assert "modsecurity" not in site


def test_write_waf_files_generates_chain(tmp_path: Path, installed) -> None:
    waf = WafConfig(enabled=True, mode="DetectionOnly", paranoia=2, inbound_threshold=7,
                    body_limit="100m", exclusions=["SecRuleRemoveById 942100"],
                    exempt_paths=["/api/method/ping", "/private/"])
    manager = _manager(tmp_path, waf)
    manager._write_waf_files()

    modsec = manager.bench.config_path / "modsecurity"
    for name in ("main.conf", "modsecurity.conf", "overrides.conf", "exclusions.conf"):
        assert (modsec / name).exists()

    main = (modsec / "main.conf").read_text()
    # Chain order: engine, CRS baseline, per-bench overrides, CRS rules, exclusions.
    assert main.index("modsecurity.conf") < main.index("crs-setup.conf")
    assert main.index("crs-setup.conf") < main.index("overrides.conf")
    assert main.index("overrides.conf") < main.index("rules/*.conf")
    assert main.index("rules/*.conf") < main.index("exclusions.conf")

    engine = (modsec / "modsecurity.conf").read_text()
    assert "SecRuleEngine DetectionOnly" in engine
    assert "SecRequestBodyLimit 104857600" in engine  # 100m in bytes
    assert "SecAuditLogFormat JSON" in engine
    # DetectionOnly must not reject oversized bodies.
    assert "SecRequestBodyLimitAction ProcessPartial" in engine

    overrides = (modsec / "overrides.conf").read_text()
    assert "tx.blocking_paranoia_level=2" in overrides
    assert "tx.inbound_anomaly_score_threshold=7" in overrides
    assert 'REQUEST_URI "@beginsWith /api/method/ping"' in overrides
    assert "ctl:ruleEngine=Off" in overrides

    assert (modsec / "exclusions.conf").read_text().strip() == "SecRuleRemoveById 942100"


def test_on_mode_rejects_oversized_body(tmp_path: Path, installed) -> None:
    manager = _manager(tmp_path, WafConfig(enabled=True, mode="On"))
    manager._write_waf_files()
    engine = (manager.bench.config_path / "modsecurity" / "modsecurity.conf").read_text()
    assert "SecRuleEngine On" in engine
    assert "SecRequestBodyLimitAction Reject" in engine


def test_write_waf_files_noop_when_disabled(tmp_path: Path, installed) -> None:
    manager = _manager(tmp_path, WafConfig(enabled=False))
    manager._write_waf_files()
    assert not (manager.bench.config_path / "modsecurity").exists()


def test_module_already_loaded_survives_unreadable_nginx_conf(tmp_path: Path, monkeypatch) -> None:
    # An unreadable/absent nginx.conf must not raise (which would break
    # install_config before rollback); treat it as "loaded" and skip injection.
    monkeypatch.setattr(nginx, "_NGINX_CONF", tmp_path / "nonexistent-nginx.conf")
    assert NginxManager._module_already_loaded() is True


# ── custom-rule compiler ──────────────────────────────────────────────────────


def test_compile_and_rule_is_a_chain() -> None:
    out = _compile([WafRule(name="Block admin abroad", action="block", match="all", conditions=[
        _cond("uri_path", "starts_with", "/admin"),
        _cond("source_ip", "is_not", "10.0.0.0/8"),
    ])])
    assert 'SecRule REQUEST_FILENAME "@beginsWith /admin" "id:100000,phase:1,deny,status:403,log,msg:\'Custom rule: Block admin abroad\',chain"' in out
    assert '    SecRule REMOTE_ADDR "!@ipMatch 10.0.0.0/8"' in out
    assert out.count("id:100000") == 1  # chained rules share the starter's id


def test_compile_any_rule_is_one_rule_per_condition() -> None:
    out = _compile([WafRule(name="Log weird", action="log", match="any", conditions=[
        _cond("user_agent", "matches", "(sqlmap|nikto)"),
        _cond("header", "is", "1", header_name="X-Debug"),
    ])])
    assert 'SecRule REQUEST_HEADERS:User-Agent "@rx (sqlmap|nikto)" "id:100000,phase:1,pass,log,auditlog' in out
    assert 'SecRule REQUEST_HEADERS:X-Debug "@streq 1" "id:100001,phase:1,pass,log,auditlog' in out
    assert "chain" not in out


def test_compile_skip_action() -> None:
    out = _compile([WafRule(name="Skip health", action="skip", conditions=[_cond("uri_path", "is", "/healthz")])])
    assert "pass,ctl:ruleEngine=Off" in out


def test_compile_source_ip_normalizes_and_ids_step_by_100() -> None:
    out = _compile([
        WafRule(name="a", conditions=[_cond("method", "is", "TRACE")]),
        WafRule(name="b", conditions=[_cond("source_ip", "is", "10.0.0.0/8, 192.168.0.1")]),
    ])
    assert "@ipMatch 10.0.0.0/8,192.168.0.1" in out  # spaces stripped
    assert "id:100000" in out and "id:100100" in out


def test_compile_skips_disabled_rules() -> None:
    assert _compile([WafRule(name="off", enabled=False, conditions=[_cond("method", "is", "TRACE")])]) == ""


def test_custom_rules_file_written_and_included_before_crs(tmp_path: Path, installed) -> None:
    config = BenchConfig._from_dict(_DATA)
    config.waf = WafConfig(enabled=True, custom_rules=[
        WafRule(name="block", conditions=[_cond("uri_path", "starts_with", "/blocked")])])
    manager = NginxManager(Bench(config, tmp_path))
    manager._write_waf_files()
    modsec = manager.bench.config_path / "modsecurity"
    assert (modsec / "custom_rules.conf").read_text().startswith("SecRule REQUEST_FILENAME")
    main = (modsec / "main.conf").read_text()
    assert main.index("overrides.conf") < main.index("custom_rules.conf") < main.index("rules/*.conf")
