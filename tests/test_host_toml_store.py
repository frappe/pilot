"""Tests for HostTomlStore, the shared host.toml read/write entry point."""

from __future__ import annotations

from pathlib import Path

from pilot.config.host_config import HostConfig
from pilot.config.host_toml_store import HostTomlStore


def test_for_bench_resolves_to_benches_directory(tmp_path: Path) -> None:
    bench_dir = tmp_path / "benches" / "mybench"
    store = HostTomlStore.for_bench(bench_dir)
    assert store.path == tmp_path / "benches" / "host.toml"


def test_read_missing_file_returns_defaults(tmp_path: Path) -> None:
    store = HostTomlStore(tmp_path)
    assert store.read() == HostConfig()


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    store = HostTomlStore(tmp_path)
    config = HostConfig(mariadb_port=3307, mariadb_root_password="s3cret", monitor_authority="alpha")
    store.write(config)
    assert store.read() == config


def test_edit_persists_only_on_change(tmp_path: Path) -> None:
    store = HostTomlStore(tmp_path)

    with store.edit() as config:
        config.monitor_authority = "alpha"
    assert store.read().monitor_authority == "alpha"

    written_at = store.path.stat().st_mtime_ns
    with store.edit():
        pass
    assert store.path.stat().st_mtime_ns == written_at


def test_read_ignores_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "host.toml").write_text('mariadb_port = 3307\nunknown_field = "x"\n')
    store = HostTomlStore(tmp_path)
    assert store.read().mariadb_port == 3307
