"""Tests for the rename-site command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.commands.rename_site import RenameSiteCommand
from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.exceptions import BenchError


def _bench(root: Path, name: str, admin_domain: str = "") -> Bench:
    bench_dir = root / "benches" / name
    bench_dir.mkdir(parents=True, exist_ok=True)
    config = BenchConfig(
        name=name,
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    config.admin.domain = admin_domain
    bench = Bench(config, bench_dir)
    bench.create_directories()
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n')
    return bench


def _make_site(bench: Bench, name: str, ssl: bool = False) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "x", "ssl": ssl}))


def test_rename_raises_if_old_missing(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    with pytest.raises(BenchError, match="does not exist"):
        RenameSiteCommand(bench, "nope.localhost", "new.localhost")._validate()


def test_rename_raises_if_new_already_exists(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    _make_site(bench, "taken.localhost")
    with pytest.raises(BenchError, match="already exists"):
        RenameSiteCommand(bench, "old.localhost", "taken.localhost")._validate()


def test_rename_raises_if_new_equals_admin_domain(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1", admin_domain="admin.localhost")
    _make_site(bench, "old.localhost")
    with pytest.raises(BenchError, match="admin domain"):
        RenameSiteCommand(bench, "old.localhost", "admin.localhost")._validate()


def test_rename_raises_if_new_claimed_by_sibling_bench(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    # A sibling bench already serves the target hostname.
    sibling = _bench(tmp_path, "b2")
    _make_site(sibling, "wanted.localhost")
    with pytest.raises(BenchError, match="already used by bench 'b2'"):
        RenameSiteCommand(bench, "old.localhost", "wanted.localhost")._validate()


def test_rename_moves_dir_and_updates_default_site(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    (bench.sites_path / "common_site_config.json").write_text(json.dumps({"default_site": "old.localhost"}))

    RenameSiteCommand(bench, "old.localhost", "new.localhost").run()

    assert not (bench.sites_path / "old.localhost").exists()
    assert (bench.sites_path / "new.localhost" / "site_config.json").exists()
    csc = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert csc["default_site"] == "new.localhost"


def test_rename_followup_runs_setup_production_when_prod(tmp_path: Path, monkeypatch) -> None:
    bench = _bench(tmp_path, "b1")
    bench.config.production.enabled = True
    calls = []
    monkeypatch.setattr(
        "pilot.commands.setup.production.SetupProductionCommand.run", lambda self: calls.append("prod")
    )
    RenameSiteCommand(bench, "old.localhost", "new.localhost")._run_followups(ssl_enabled=True)
    assert calls == ["prod"]  # prod path covers TLS; letsencrypt not run separately


def test_rename_followup_runs_letsencrypt_when_ssl_and_not_prod(tmp_path: Path, monkeypatch) -> None:
    bench = _bench(tmp_path, "b1")
    calls = []
    monkeypatch.setattr(
        "pilot.commands.setup.letsencrypt.SetupLetsEncryptCommand.run", lambda self: calls.append("le")
    )
    RenameSiteCommand(bench, "old.localhost", "new.localhost")._run_followups(ssl_enabled=True)
    assert calls == ["le"]


def test_rename_followup_advises_on_failure(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture) -> None:
    bench = _bench(tmp_path, "b1")
    bench.config.production.enabled = True

    def boom(self):
        raise BenchError("nginx exploded")

    monkeypatch.setattr("pilot.commands.setup.production.SetupProductionCommand.run", boom)
    RenameSiteCommand(bench, "old.localhost", "new.localhost")._run_followups(ssl_enabled=False)
    out = capsys.readouterr().out
    assert "did not complete" in out
    assert "bench setup production -b b1" in out
