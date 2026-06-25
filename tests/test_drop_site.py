"""Tests for DropSiteCommand._deregister_domains driving a dummy provider."""
import json
import os
from pathlib import Path

import pytest

from bench_cli.commands.drop_site import DropSiteCommand
from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError

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


def test_deregisters_every_domain(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite", {"domains": ["app.example.com", "shop.example.com"],
                                  "host_name": "https://app.example.com"})

    DropSiteCommand(bench, "mysite")._deregister_domains()

    assert log.read_text().splitlines() == ["deregister app.example.com", "deregister shop.example.com"]


def test_halts_on_provider_failure(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    monkeypatch.setenv("PROVIDER_FAIL", "deregister")
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite", {"domains": ["app.example.com"]})

    with pytest.raises(BenchError, match="deregister declined"):
        DropSiteCommand(bench, "mysite")._deregister_domains()


def test_no_op_for_missing_site(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    DropSiteCommand(_make_bench(tmp_path), "ghost")._deregister_domains()
    assert not log.exists()
