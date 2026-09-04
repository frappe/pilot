"""Tests for ProductionSetup helpers and letsencrypt gating."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.setup import ProductionSetup
from pilot.exceptions import BenchError
from pilot.managers.letsencrypt import is_letsencrypt_required


def _make_bench(
    tmp_path: Path,
    name: str = "prod",
    *,
    admin_domain: str = "prod-admin.localhost",
    email: str = "",
    process_manager: str = "supervisor",
    tls: bool = True,
) -> Bench:
    from pilot.config.common import CommonConfig
    from pilot.config.letsencrypt import LetsEncryptConfig
    from pilot.config.mariadb import MariaDBConfig

    benches_dir = tmp_path / "benches"
    bench_dir = benches_dir / name
    (bench_dir / "sites").mkdir(parents=True, exist_ok=True)
    # mariadb/letsencrypt are host-shared state (common_config.toml), not
    # bench.toml fields, since this change.
    CommonConfig(
        mariadb=MariaDBConfig(root_password="root"),
        letsencrypt=LetsEncryptConfig(email=email),
    ).write(benches_dir)
    (bench_dir / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        "[redis]\ncache_port = 13000\nqueue_port = 11000\n\n"
        f'[admin]\ndomain = "{admin_domain}"\ntls = {"true" if tls else "false"}\n\n'
        f'[production]\nprocess_manager = "{process_manager}"\n'
    )
    config = BenchConfig.from_file(bench_dir / "bench.toml")
    return Bench(config, bench_dir)


def _make_site(bench: Bench, name: str, *, ssl: bool = False) -> Path:
    site_path = bench.sites_path / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"db_name": "site", "ssl": ssl}))
    return site_path


def test_persist_preserves_other_fields(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    cmd = ProductionSetup(bench)
    cmd._persist({"admin": {"domain": "admin.example.com"}})

    data = tomllib.loads((bench.path / "bench.toml").read_text())
    assert data["admin"]["domain"] == "admin.example.com"
    # Untouched sections survive the rewrite.
    assert data["production"]["process_manager"] == "supervisor"
    assert data["apps"][0]["name"] == "frappe"
    # mariadb lives in common_config.toml now, untouched by this rewrite too.
    assert bench.config.mariadb.root_password == "root"


def test_write_dns_multitenancy_creates_missing_sites_directory(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    bench.sites_path.rmdir()

    assert not bench.sites_path.exists()

    ProductionSetup(bench)._write_dns_multitenancy()

    common_config = bench.sites_path / "common_site_config.json"
    assert bench.sites_path.is_dir()
    assert common_config.is_file()
    assert json.loads(common_config.read_text())["dns_multitenant"] == 1


def test_check_admin_domain_uses_toml_value(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="keep.example.com")
    cmd = ProductionSetup(bench)
    cmd._check_admin_domain()  # must not prompt or raise
    assert bench.config.admin.domain == "keep.example.com"


def test_check_admin_domain_rejects_sibling_owned(tmp_path: Path) -> None:
    _make_bench(tmp_path, name="other", admin_domain="shared.example.com")
    bench = _make_bench(tmp_path, name="prod", admin_domain="shared.example.com")
    cmd = ProductionSetup(bench)
    with pytest.raises(BenchError, match="already used by bench 'other'"):
        cmd._check_admin_domain()


def test_check_admin_domain_grandfathers_existing_non_matching(tmp_path: Path, monkeypatch) -> None:
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    monkeypatch.setattr(
        DomainRouteProvider, "wildcard_domains", staticmethod(lambda: ["*.node1.example.com"])
    )
    bench = _make_bench(tmp_path, admin_domain="node1.example.com")  # apex, can't match wildcard
    ProductionSetup(bench)._check_admin_domain()  # existing -> no raise

    cmd = ProductionSetup(bench, admin_domain="other.example.com")
    cmd._resolve_target()
    with pytest.raises(BenchError, match="must match one of this bench's wildcard"):
        cmd._check_admin_domain()


def test_is_letsencrypt_required(tmp_path: Path) -> None:
    # Public admin domain + email → cert needed.
    assert is_letsencrypt_required(
        _make_bench(tmp_path, name="a", admin_domain="admin.example.com", email="x@y.com")
    )
    # No email → never.
    assert not is_letsencrypt_required(_make_bench(tmp_path, name="b", admin_domain="admin.example.com"))
    # Local dev domain → not obtainable.
    assert not is_letsencrypt_required(
        _make_bench(tmp_path, name="c", admin_domain="c-admin.localhost", email="x@y.com")
    )
    # TLS disabled (central proxy terminates TLS) → no admin cert needed.
    assert not is_letsencrypt_required(
        _make_bench(tmp_path, name="d", admin_domain="admin.example.com", email="x@y.com", tls=False)
    )


def test_resolve_target_uses_flag_over_config(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    cmd = ProductionSetup(bench, process_manager="systemd")
    cmd._resolve_target()
    assert bench.config.production.process_manager == "systemd"
    assert bench.config.production.enabled is True
    # Production must enable the admin so it's reachable behind its domain.
    assert bench.config.admin.enabled is True


def test_resolve_target_applies_tls_flag(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, tls=True)
    cmd = ProductionSetup(bench, process_manager="systemd", admin_tls=False)
    cmd._resolve_target()
    assert bench.config.admin.tls is False


def test_resolve_target_normalizes_supervisord(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    cmd = ProductionSetup(bench, process_manager="supervisord")
    cmd._resolve_target()
    assert bench.config.production.process_manager == "supervisor"


def test_resolve_target_defaults_to_systemd(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="none")
    cmd = ProductionSetup(bench)
    cmd._resolve_target()
    assert bench.config.production.process_manager == "systemd"
    assert bench.config.production.enabled is True


def test_resolve_target_applies_admin_domain(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    cmd = ProductionSetup(bench, process_manager="systemd", admin_domain="admin-new.example.com")
    cmd._resolve_target()
    assert bench.config.admin.domain == "admin-new.example.com"


def test_resolve_target_applies_letsencrypt_email(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    cmd = ProductionSetup(bench, process_manager="systemd", letsencrypt_email="me@example.com")
    cmd._resolve_target()
    assert bench.config.letsencrypt.email == "me@example.com"


def test_require_production_inputs_needs_admin_domain(tmp_path: Path) -> None:
    # Fresh, undeployed bench: empty domain, no process manager yet (so it loads).
    bench = _make_bench(tmp_path, admin_domain="", process_manager="")
    cmd = ProductionSetup(bench, process_manager="systemd")
    cmd._resolve_target()
    with pytest.raises(BenchError, match="admin domain is required"):
        cmd._require_production_inputs()


def test_require_production_inputs_needs_email_for_tls(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="admin.example.com", tls=True, email="")
    cmd = ProductionSetup(bench, process_manager="systemd")
    cmd._resolve_target()
    with pytest.raises(BenchError, match="contact email is required"):
        cmd._require_production_inputs()


def test_require_production_inputs_passes_with_domain_and_email(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="admin.example.com", tls=True, email="me@example.com")
    cmd = ProductionSetup(bench, process_manager="systemd")
    cmd._resolve_target()
    cmd._require_production_inputs()  # no raise


def test_require_production_inputs_needs_email_for_existing_public_site(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="admin.localhost", email="")
    _make_site(bench, "site.example.com")
    cmd = ProductionSetup(bench)

    with pytest.raises(BenchError, match="contact email is required"):
        cmd._require_production_inputs()


def test_setup_monitoring_runs_privileged_setup_at_provision_time(tmp_path: Path, monkeypatch) -> None:
    """Privileged log-dir/logrotate setup must run here, not in the user-service daemons."""
    from pilot.core.server.monitoring import MonitorConfigurator
    from pilot.core.site.storage.systemd import SiteStorageConfigurator
    from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator

    bench = _make_bench(tmp_path, process_manager="systemd")
    bench.config.production.enabled = True
    cmd = ProductionSetup(bench)

    called = []
    monkeypatch.setattr(MonitorConfigurator, "install", lambda self: None)
    monkeypatch.setattr(UptimeMonitorConfigurator, "install", lambda self: None)
    monkeypatch.setattr(SiteStorageConfigurator, "install", lambda self: None)
    monkeypatch.setattr(MonitorConfigurator, "setup", lambda self: called.append("monitor"))
    monkeypatch.setattr(UptimeMonitorConfigurator, "setup", lambda self: called.append("uptime"))

    cmd._setup_monitoring()

    assert called == ["monitor", "uptime"]


class _BlockPsutil:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "psutil":
            raise ImportError("psutil is not installed on the system python")


def test_setup_monitoring_runs_without_psutil(tmp_path: Path, monkeypatch) -> None:
    """The CLI runs on the system python, which has no third-party packages."""
    from pilot.core.server.monitoring_config import MonitorConfigurator
    from pilot.core.site.storage.systemd import SiteStorageConfigurator
    from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator

    bench = _make_bench(tmp_path, process_manager="systemd")
    cmd = ProductionSetup(bench)

    monkeypatch.setattr(SiteStorageConfigurator, "install", lambda self: None)
    for configurator in (MonitorConfigurator, UptimeMonitorConfigurator):
        monkeypatch.setattr(configurator, "install", lambda self: None)
        monkeypatch.setattr(configurator, "setup", lambda self: None)

    for module in ("psutil", "pilot.core.server.monitoring", "pilot.core.server.monitoring_proc"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockPsutil(), *sys.meta_path])

    cmd._setup_monitoring()


def test_setup_letsencrypt_reraises_by_default(tmp_path: Path, monkeypatch) -> None:
    from pilot.core.bench import Bench

    bench = _make_bench(tmp_path, admin_domain="admin.example.com", email="x@y.com")
    monkeypatch.setattr(
        Bench,
        "setup_letsencrypt",
        lambda self: (_ for _ in ()).throw(RuntimeError("dns not ready")),
    )
    cmd = ProductionSetup(bench)

    with pytest.raises(RuntimeError, match="dns not ready"):
        cmd._setup_letsencrypt_if_needed()


def test_setup_letsencrypt_swallows_when_best_effort(tmp_path: Path, monkeypatch, capsys) -> None:
    """Wizard handoff keeps HTTP live when TLS is not ready yet."""
    from pilot.core.bench import Bench

    bench = _make_bench(tmp_path, admin_domain="admin.example.com", email="x@y.com")
    monkeypatch.setattr(
        Bench,
        "setup_letsencrypt",
        lambda self: (_ for _ in ()).throw(RuntimeError("dns not ready")),
    )
    cmd = ProductionSetup(bench, best_effort_tls=True)

    cmd._setup_letsencrypt_if_needed()  # must not raise

    assert "dns not ready" in capsys.readouterr().err


def test_setup_letsencrypt_enables_existing_public_sites(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="admin.localhost", email="x@y.com")
    site_path = _make_site(bench, "site.example.com")

    with patch.object(Bench, "setup_letsencrypt") as setup_letsencrypt:
        ProductionSetup(bench)._setup_letsencrypt_if_needed()

    setup_letsencrypt.assert_called_once_with()
    assert json.loads((site_path / "site_config.json").read_text())["ssl"] is True


def test_persist_production_state_writes_enabled_and_drops_nginx(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    # legacy nginx key present in toml
    toml_path = bench.path / "bench.toml"
    toml_path.write_text(
        toml_path.read_text().replace(
            '[production]\nprocess_manager = "supervisor"\n',
            '[production]\nprocess_manager = "supervisor"\nnginx = true\n',
        )
    )
    cmd = ProductionSetup(bench, process_manager="systemd")
    cmd._resolve_target()
    cmd._persist_production_state()
    data = tomllib.loads(toml_path.read_text())
    assert data["production"]["enabled"] is True
    assert data["production"]["process_manager"] == "systemd"
    assert "nginx" not in data["production"]
    assert data["admin"]["tls"] is True
    assert data["admin"]["enabled"] is True
