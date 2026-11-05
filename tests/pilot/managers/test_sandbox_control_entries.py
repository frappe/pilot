from __future__ import annotations

import re
from pathlib import Path

import pytest

from pilot.config.common_site_config import update_common_site_config
from pilot.managers.processes.sandbox import (
    CONTROL_DIRECTORIES,
    CONTROL_ENTRIES,
    CONTROL_FILES,
    WORKLOAD_DIRECTORIES,
    create_sandbox_entries,
)
from pilot.managers.redis import RedisManager
from tests.pilot.commands.test_commands import make_bench

ROOT = Path(__file__).parents[3]
# Names under config/ that Pilot's own code uses. Each one the admin trusts must be protected.
CONFIG_NAME = re.compile(r'config_path\s*/\s*"([^"]+)"|"config"\s*/\s*"([^"]+)"')
# Files in config/ that a workload may create, besides the lock files Frappe writes there.
WRITABLE_BY_WORKLOAD = {"socketio.sock"}


def get_config_names() -> set[str]:
    names: set[str] = set()
    for folder in ("pilot", "admin"):
        for path in (ROOT / folder).rglob("*.py"):
            if "_vendor" in path.parts or "node_modules" in path.parts:
                continue
            for first, second in CONFIG_NAME.findall(path.read_text()):
                names.add(first or second)
    return names


def test_every_config_path_pilot_uses_is_protected_or_listed_as_writable() -> None:
    unknown = get_config_names() - set(CONTROL_ENTRIES) - WRITABLE_BY_WORKLOAD

    assert not unknown, f"Protect these in the sandbox or list them as writable: {sorted(unknown)}"


def test_the_redis_config_files_are_protected(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    servers = RedisManager(bench.config.redis, bench).servers

    assert set(servers) <= set(CONTROL_FILES)


def test_protected_names_are_unique() -> None:
    assert len(CONTROL_ENTRIES) == len(set(CONTROL_ENTRIES))


def test_the_protected_folders_exist_before_the_sandbox_starts(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()

    create_sandbox_entries(bench)

    for name in WORKLOAD_DIRECTORIES:
        assert (bench.path / name).is_dir()
    for name in CONTROL_DIRECTORIES:
        assert (bench.config_path / name).is_dir()
    assert (bench.config_path / "site-aliases.json").read_text().strip() == "{}"
    assert (bench.config_path / "realtime-folders.txt").is_file()


def test_creating_the_protected_entries_keeps_what_is_there(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.config_path / "site-aliases.json").write_text('{"old": 1}')

    create_sandbox_entries(bench)

    assert (bench.config_path / "site-aliases.json").read_text() == '{"old": 1}'


@pytest.mark.parametrize("name", ["site_config.lock", "flow_server.lock", "bench_build.lock"])
def test_frappe_lock_files_are_not_protected(name: str) -> None:
    assert name not in CONTROL_ENTRIES


def test_recording_a_site_alias_keeps_the_file_a_sandboxed_unit_binds(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    create_sandbox_entries(bench)
    registry = bench.site_aliases.registry_path
    inode = registry.stat().st_ino

    bench.site_aliases.record("old.example.com")

    assert registry.stat().st_ino == inode
    assert "old.example.com" in registry.read_text()


def test_updating_common_site_config_keeps_the_file_a_sandboxed_unit_binds(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    path = bench.sites_path / "common_site_config.json"
    path.write_text("{}")
    inode = path.stat().st_ino

    with update_common_site_config(bench.sites_path) as config:
        config["allow_cors"] = "*"

    assert path.stat().st_ino == inode
    assert '"allow_cors": "*"' in path.read_text()
