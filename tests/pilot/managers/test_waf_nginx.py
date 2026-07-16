"""Tests for ModSecurity (WAF) directive rendering and rule-file generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.site_config import SiteConfig
from pilot.config.waf_config import WafConfig
from pilot.core.bench import Bench
from pilot.managers import nginx
from pilot.managers.nginx import NginxManager

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


def _manager(tmp_path: Path, waf: WafConfig) -> NginxManager:
    config = BenchConfig._from_dict(_DATA)
    config.waf = waf
    return NginxManager(Bench(config, tmp_path))


def test_render_waf_empty_when_disabled(tmp_path: Path, installed) -> None:
    assert _manager(tmp_path, WafConfig(enabled=False))._render_waf() == ""


def test_render_waf_empty_when_not_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(nginx.WafManager, "is_installed", staticmethod(lambda: False))
    assert _manager(tmp_path, WafConfig(enabled=True))._render_waf() == ""


def test_render_waf_emits_directives_when_active(tmp_path: Path, installed) -> None:
    out = _manager(tmp_path, WafConfig(enabled=True))._render_waf()
    assert "modsecurity on;" in out
    assert "modsecurity_rules_file" in out and "modsecurity/main.conf" in out


def test_site_and_admin_vhosts_carry_waf(tmp_path: Path, installed) -> None:
    manager = _manager(tmp_path, WafConfig(enabled=True))
    site = manager._render_http_only_block(_SITE, "test-bench", manager.bench.config.nginx, manager.bench.path)
    admin = manager._generate_admin_config(ssl_ready=False)
    assert "modsecurity on;" in site
    assert "modsecurity on;" in admin


def test_vhosts_clean_when_disabled(tmp_path: Path, installed) -> None:
    manager = _manager(tmp_path, WafConfig(enabled=False))
    site = manager._render_http_only_block(_SITE, "test-bench", manager.bench.config.nginx, manager.bench.path)
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
