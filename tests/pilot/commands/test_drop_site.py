"""Tests for Site.drop()'s provider domain capture/release driving a dummy provider."""

import json
import os
from pathlib import Path

from pilot.config import BenchConfig, SiteConfig
from pilot.core.bench import Bench
from pilot.core.site import Site

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

# Logs argv to $PROVIDER_LOG; $PROVIDER_FAIL=<verb> makes that verb exit non-zero.
_DUMMY_PROVIDER = """#!/usr/bin/env python3
import os, sys
argv = sys.argv[1:]
log = os.environ.get("PROVIDER_LOG")
if log:
    open(log, "a").write(" ".join(argv) + "\\n")
if os.environ.get("PROVIDER_FAIL") == (argv[0] if argv else ""):
    sys.stderr.write(argv[0] + " declined")
    sys.exit(2)
sys.exit(0)
"""


def _install_provider(tmp_path: Path, monkeypatch) -> Path:
    exe = tmp_path / "bin" / "bench-domain-provider"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(_DUMMY_PROVIDER)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(exe.parent) + os.pathsep + os.environ["PATH"])
    log = tmp_path / "calls.log"
    monkeypatch.setenv("PROVIDER_LOG", str(log))
    return log


def _make_bench(tmp_path: Path) -> Bench:
    return Bench(BenchConfig._from_dict(_BENCH_DATA), tmp_path)


def _write_site(bench: Bench, name: str, config: dict) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def test_collects_site_name_and_custom_domains(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(
        bench,
        "mysite",
        {
            "domains": ["app.example.com", "shop.example.com"],
            "host_name": "https://app.example.com",
        },
    )

    assert Site(SiteConfig(name="mysite", apps=[]), bench)._provider_domains() == [
        "mysite",
        "app.example.com",
        "shop.example.com",
    ]


def test_releases_every_captured_domain(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)

    Site(SiteConfig(name="mysite", apps=[]), bench)._release_domains(["mysite", "app.example.com"])

    assert log.read_text().splitlines() == [
        "deregister mysite",
        "deregister app.example.com",
    ]


def test_release_swallows_provider_failure(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    monkeypatch.setenv("PROVIDER_FAIL", "deregister")
    bench = _make_bench(tmp_path)

    # Best effort after a successful drop: a provider failure must not raise.
    Site(SiteConfig(name="mysite", apps=[]), bench)._release_domains(["app.example.com"])


def test_no_domains_without_site_config(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)

    assert Site(SiteConfig(name="mysite", apps=[]), bench)._provider_domains() == []


def test_no_op_for_missing_site(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    cmd = Site(SiteConfig(name="ghost", apps=[]), _make_bench(tmp_path))
    cmd._release_domains(cmd._provider_domains())
    assert not log.exists()


def _capture_drop_cmd(tmp_path: Path, monkeypatch, bench: Bench) -> dict:
    bench.config.write(bench.path)
    _write_site(bench, "mysite", {})
    captured: dict = {}
    monkeypatch.setattr("pilot.core.site.run_command", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    Site(SiteConfig(name="mysite", apps=[]), bench).drop()
    return captured


def test_drop_uses_postgres_root_creds(tmp_path: Path, monkeypatch) -> None:
    # The drop connects to the server as root to drop the database, so it must pass
    # the bench engine's credentials - postgres password auth fails without them.
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    bench.config.db_type = "postgres"
    bench.config.postgres.root_password = "pgpw"

    cmd = _capture_drop_cmd(tmp_path, monkeypatch, bench)["cmd"]
    assert "drop-site" in cmd
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_drop_uses_mariadb_root_creds(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)  # mariadb, root_password "root"

    cmd = _capture_drop_cmd(tmp_path, monkeypatch, bench)["cmd"]
    assert cmd[cmd.index("--db-root-username") + 1] == "root"
    assert cmd[cmd.index("--db-root-password") + 1] == "root"
