"""Tests for BenchConfig.from_flat port offsets."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pilot.config import BenchConfig


def test_default_ports_returns_all_fields() -> None:
    ports = BenchConfig.default_ports()
    assert set(ports) == {
        "http_port",
        "socketio_port",
        "redis.cache_port",
        "redis.queue_port",
        "admin.port",
    }


def test_default_ports_values_match_known_defaults() -> None:
    ports = BenchConfig.default_ports()
    assert ports["http_port"] == 8000
    assert ports["socketio_port"] == 9000
    assert ports["redis.cache_port"] == 13000
    assert ports["redis.queue_port"] == 11000
    assert ports["admin.port"] == 7000


def _render(tmp_path: Path, settings: dict | None = None, port_offset: int = 0) -> dict:
    path = tmp_path / "bench.toml"
    path.write_text(BenchConfig.from_flat("my-bench", settings, port_offset=port_offset).dumps())
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_port_offset_zero_leaves_defaults(tmp_path: Path) -> None:
    data = _render(tmp_path)
    assert data["bench"]["http_port"] == 8000
    assert data["admin"]["port"] == 7000


def test_port_offset_shifts_all_fields_together(tmp_path: Path) -> None:
    data = _render(tmp_path, port_offset=1)
    assert data["bench"]["http_port"] == 8001
    assert data["bench"]["socketio_port"] == 9001
    assert data["redis"]["cache_port"] == 13001
    assert data["redis"]["queue_port"] == 11001
    assert data["admin"]["port"] == 7001
    # mariadb.port is deliberately NOT offset: every bench for this OS user
    # shares one MariaDB server, so it must stay identical across benches.
    assert data["mariadb"]["port"] == 3306


def test_port_fields_not_settable_via_settings(tmp_path: Path) -> None:
    """Regression: flat settings cannot override offset-managed ports."""
    data = _render(tmp_path, settings={"admin_port": 9999, "http_port": 1234}, port_offset=1)
    assert data["bench"]["http_port"] == 8001
    assert data["admin"]["port"] == 7001


def test_current_port_offset_reads_http_port(tmp_path: Path) -> None:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchConfig.from_flat("my-bench", port_offset=3).dumps())
    assert BenchConfig.current_port_offset(toml_path) == 3


def test_current_port_offset_zero_when_file_missing(tmp_path: Path) -> None:
    assert BenchConfig.current_port_offset(tmp_path / "bench.toml") == 0


def test_current_port_offset_zero_when_file_invalid(tmp_path: Path) -> None:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text("not valid toml {{{")
    assert BenchConfig.current_port_offset(toml_path) == 0


def test_mariadb_host_and_existing_round_trip(tmp_path: Path) -> None:
    settings = {
        "mariadb_existing": True,
        "mariadb_host": "db.example.com",
        "mariadb_admin_user": "admin",
    }
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchConfig.from_flat("my-bench", settings).dumps())

    read_back = BenchConfig.read_flat(toml_path)
    assert read_back["mariadb_existing"] is True
    assert read_back["mariadb_host"] == "db.example.com"


def test_existing_defaults_to_false_when_unset(tmp_path: Path) -> None:
    data = _render(tmp_path)
    assert data["mariadb"]["existing"] is False
    assert data["postgres"]["existing"] is False
