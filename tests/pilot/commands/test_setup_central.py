from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.commands.setup.central import SetupCentralCommand
from pilot.config import AppConfig, BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig, WorkerGroup
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError


def _bench(root: Path, name: str = "b1", admin_domain: str = "admin.example.com") -> Bench:
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


def _make_site(bench: Bench, name: str) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "x"}))


def _command(bench: Bench, **kwargs) -> SetupCentralCommand:
    return SetupCentralCommand(bench=bench, **kwargs)


def _central(bench: Bench):
    return CommonConfig.read(bench.path.parent).central


def test_enables_central_and_writes_both_aliases(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")

    _command(
        bench,
        admin_pattern="admin-vm-*.example.com",
        site_pattern="site-*.example.com",
    ).run()

    central = _central(bench)
    assert central.enabled is True
    assert central.bootstrapped is False
    assert [(alias.type, alias.pattern, alias.target, alias.redirect) for alias in central.hostname_aliases] == [
        ("admin", "admin-vm-*.example.com", "admin.example.com", False),
        ("site", "site-*.example.com", "site1.local", False),
    ]


def test_enables_central_without_any_alias(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    _command(bench).run()

    central = _central(bench)
    assert central.enabled is True
    assert central.hostname_aliases == []


def test_rewriting_a_pattern_replaces_its_alias(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    _command(bench, site_pattern="site-*.example.com").run()

    _command(bench, site_pattern="site-*.example.com", redirect=True).run()

    aliases = _central(bench).hostname_aliases
    assert [(alias.pattern, alias.redirect) for alias in aliases] == [("site-*.example.com", True)]


def test_keeps_the_bootstrap_flag_unless_asked(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    with CommonConfig.open(bench.path.parent) as common:
        common.central.enabled = True
        common.central.bootstrapped = True

    _command(bench).run()
    assert _central(bench).bootstrapped is True

    _command(bench, rebootstrap=True).run()
    assert _central(bench).bootstrapped is False


def test_rejects_a_second_bench_on_the_host(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _bench(tmp_path, "b2")

    with pytest.raises(BenchError, match="also has: b2"):
        _command(bench, admin_pattern="admin-vm-*.example.com").run()
    assert _central(bench).enabled is False


def test_rejects_a_pattern_without_a_vm_placeholder(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    with pytest.raises(BenchError, match="exactly one '\\*'"):
        _command(bench, admin_pattern="admin.example.com").run()
    assert _central(bench).enabled is False


def test_rejects_an_admin_alias_without_an_admin_domain(tmp_path: Path) -> None:
    bench = _bench(tmp_path, admin_domain="")

    with pytest.raises(BenchError, match="no admin domain"):
        _command(bench, admin_pattern="admin-vm-*.example.com").run()


@pytest.mark.parametrize("sites", [[], ["one.local", "two.local"]])
def test_rejects_a_site_alias_unless_the_bench_has_one_site(tmp_path: Path, sites: list[str]) -> None:
    bench = _bench(tmp_path)
    for site in sites:
        _make_site(bench, site)

    with pytest.raises(BenchError, match="only site"):
        _command(bench, site_pattern="site-*.example.com").run()
    assert _central(bench).enabled is False
