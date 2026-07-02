"""Tests for BenchTomlStore, the single read/write entry point for bench.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import ConfigError


def _write_bench(bench_dir: Path, name: str = "test") -> BenchTomlStore:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchTomlBuilder(name).render())
    return BenchTomlStore.for_bench(bench_dir)


def test_for_bench_resolves_toml_path(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    assert store.path == tmp_path / "bench.toml"


def test_accepts_directory_or_file(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text(BenchTomlBuilder("x").render())
    assert BenchTomlStore(tmp_path).path == BenchTomlStore(tmp_path / "bench.toml").path


def test_exists_reflects_file(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    assert not store.exists()
    _write_bench(tmp_path)
    assert store.exists()


def test_read_returns_validated_config(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "mybench")
    assert store.read().name == "mybench"


def test_read_no_validate_allows_half_configured(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text('[bench]\nname = "half"\n')
    store = BenchTomlStore.for_bench(tmp_path)
    with pytest.raises(ConfigError):
        store.read()
    assert store.read(validate=False).name == "half"


def test_read_raw_preserves_unmodeled_sections(tmp_path: Path) -> None:
    store = _write_bench(tmp_path)
    raw = store.read_raw()
    raw["sites"] = [{"name": "site1"}]
    store.write_raw(raw)
    assert store.read_raw()["sites"] == [{"name": "site1"}]


def test_read_flat_matches_builder(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "flatbench")
    assert store.read_flat()["bench_name"] == "flatbench"


def test_write_round_trips_config(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "rt")
    config = store.read()
    config.http_port = 8123
    store.write(config)
    assert store.read().http_port == 8123


def test_write_flat_serialises_settings(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("flatwrite", {"python": "3.13"})
    config = store.read()
    assert config.name == "flatwrite"
    assert config.python_version == "3.13"


def test_write_flat_matches_builder(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("b", {"python": "3.12"}, port_offset=5)
    assert store.read_flat() == BenchTomlBuilder.read_settings(store.path)


def test_write_flat_preserves_production_enabled(tmp_path: Path) -> None:
    """production.enabled has no flat key, so BenchTomlBuilder.build() always
    reconstructs it as the dataclass default (False). A wizard/settings save
    (write_flat) on a bench already brought up to production must not silently
    demote it back to "development" — that's a regression, not a real change."""
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("prod-bench", {"production_process_manager": "systemd", "admin_domain": "admin.example.com"})
    raw = store.read_raw()
    raw["production"]["enabled"] = True
    store.write_raw(raw)

    store.write_flat("prod-bench", {
        "production_process_manager": "systemd",
        "admin_domain": "admin.example.com",
        "admin_password": "secret",
    })

    config = store.read()
    assert config.production.enabled is True
    assert config.production.process_manager == "systemd"
    assert config.admin.password == "secret"
