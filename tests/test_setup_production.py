"""Tests for SetupProductionCommand helpers and letsencrypt gating.

The full `run()` touches sudo/systemd, so these exercise the pure helpers:
admin-domain handling, in-place toml persistence, and the cert-needed check.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bench_cli.commands.setup.production import SetupProductionCommand
from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError
from bench_cli.managers.letsencrypt_manager import needs_letsencrypt


def _make_bench(tmp_path: Path, name: str = "prod", *, admin_domain: str = "prod-admin.localhost",
                email: str = "", process_manager: str = "supervisor", tls: bool = True) -> Bench:
    bench_dir = tmp_path / "benches" / name
    (bench_dir / "sites").mkdir(parents=True, exist_ok=True)
    le = f'\n[letsencrypt]\nemail = "{email}"\n' if email else ""
    (bench_dir / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[redis]\ncache_port = 13000\nqueue_port = 11000\n\n'
        f'[admin]\ndomain = "{admin_domain}"\ntls = {"true" if tls else "false"}\n'
        f'{le}\n'
        f'[production]\nprocess_manager = "{process_manager}"\n'
    )
    config = BenchConfig.from_file(bench_dir / "bench.toml")
    return Bench(config, bench_dir)


def test_persist_preserves_other_fields(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    cmd = SetupProductionCommand(bench)
    cmd._persist({"admin": {"domain": "admin.example.com"}})

    data = tomllib.loads((bench.path / "bench.toml").read_text())
    assert data["admin"]["domain"] == "admin.example.com"
    # Untouched sections survive the rewrite.
    assert data["production"]["process_manager"] == "supervisor"
    assert data["mariadb"]["root_password"] == "root"
    assert data["apps"][0]["name"] == "frappe"


def test_check_admin_domain_uses_toml_value(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, admin_domain="keep.example.com")
    cmd = SetupProductionCommand(bench)
    cmd._check_admin_domain()  # must not prompt or raise
    assert bench.config.admin.domain == "keep.example.com"


def test_check_admin_domain_rejects_sibling_owned(tmp_path: Path) -> None:
    _make_bench(tmp_path, name="other", admin_domain="shared.example.com")
    bench = _make_bench(tmp_path, name="prod", admin_domain="shared.example.com")
    cmd = SetupProductionCommand(bench)
    with pytest.raises(BenchError, match="already used by bench 'other'"):
        cmd._check_admin_domain()


def test_needs_letsencrypt(tmp_path: Path) -> None:
    # Public admin domain + email → cert needed.
    assert needs_letsencrypt(_make_bench(tmp_path, name="a", admin_domain="admin.example.com", email="x@y.com"))
    # No email → never.
    assert not needs_letsencrypt(_make_bench(tmp_path, name="b", admin_domain="admin.example.com"))
    # Local dev domain → not obtainable.
    assert not needs_letsencrypt(_make_bench(tmp_path, name="c", admin_domain="c-admin.localhost", email="x@y.com"))
    # TLS disabled (central proxy terminates TLS) → no admin cert needed.
    assert not needs_letsencrypt(
        _make_bench(tmp_path, name="d", admin_domain="admin.example.com", email="x@y.com", tls=False)
    )


# ── --process-manager / persist-last / migration ────────────────────────────────


def test_resolve_target_uses_flag_over_config(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    cmd = SetupProductionCommand(bench, process_manager="systemd")
    cmd._resolve_target()
    assert bench.config.production.process_manager == "systemd"
    assert bench.config.production.enabled is True


def test_resolve_target_normalizes_supervisord(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    cmd = SetupProductionCommand(bench, process_manager="supervisord")
    cmd._resolve_target()
    assert bench.config.production.process_manager == "supervisor"


def test_resolve_target_errors_without_manager(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="none")
    cmd = SetupProductionCommand(bench)
    with pytest.raises(BenchError, match="No process manager"):
        cmd._resolve_target()


def test_resolve_target_applies_admin_domain(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    cmd = SetupProductionCommand(bench, process_manager="systemd", admin_domain="admin-new.example.com")
    cmd._resolve_target()
    assert bench.config.admin.domain == "admin-new.example.com"


def test_persist_production_state_writes_enabled_and_drops_nginx(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, process_manager="supervisor")
    # legacy nginx key present in toml
    toml_path = bench.path / "bench.toml"
    toml_path.write_text(toml_path.read_text().replace(
        '[production]\nprocess_manager = "supervisor"\n',
        '[production]\nprocess_manager = "supervisor"\nnginx = true\n',
    ))
    cmd = SetupProductionCommand(bench, process_manager="systemd")
    cmd._resolve_target()
    cmd._persist_production_state()
    data = tomllib.loads(toml_path.read_text())
    assert data["production"]["enabled"] is True
    assert data["production"]["process_manager"] == "systemd"
    assert "nginx" not in data["production"]
    assert data["admin"]["tls"] is True
