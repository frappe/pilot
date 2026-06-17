"""Tests for BenchTomlBuilder's port-offset handling.

Ports are deliberately not wizard-editable (kept out of FLAT_KEYS) so a new
bench can get an auto-picked, collision-free offset; these tests guard both
that exclusion and the offset application itself.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from bench_cli.config.bench_toml_builder import BenchTomlBuilder, current_port_offset, default_ports

# ── default_ports ────────────────────────────────────────────────────────────


def test_default_ports_returns_all_fields() -> None:
    ports = default_ports()
    assert set(ports) == {
        "http_port", "socketio_port", "redis.cache_port", "redis.queue_port", "admin.port", "mariadb.port",
    }


def test_default_ports_values_match_known_defaults() -> None:
    ports = default_ports()
    assert ports["http_port"] == 8000
    assert ports["socketio_port"] == 9000
    assert ports["redis.cache_port"] == 13000
    assert ports["redis.queue_port"] == 11000
    assert ports["admin.port"] == 7000
    assert ports["mariadb.port"] == 3306


# ── BenchTomlBuilder port_offset ─────────────────────────────────────────────


def _render(tmp_path: Path, settings: dict | None = None, port_offset: int = 0) -> dict:
    path = tmp_path / "bench.toml"
    path.write_text(BenchTomlBuilder("my-bench", settings, port_offset=port_offset).render())
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
    assert data["mariadb"]["port"] == 3307


def test_port_fields_not_settable_via_settings(tmp_path: Path) -> None:
    """Regression: admin_port used to stay in FLAT_KEYS, so a caller that
    carried its current value forward in the settings dict (as the setup
    wizard's save step does) got it offset twice. Settings can no longer
    touch any port field — only port_offset can."""
    data = _render(tmp_path, settings={"admin_port": 9999, "http_port": 1234}, port_offset=1)
    assert data["bench"]["http_port"] == 8001
    assert data["admin"]["port"] == 7001


# ── current_port_offset ──────────────────────────────────────────────────────


def test_current_port_offset_reads_http_port(tmp_path: Path) -> None:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchTomlBuilder("my-bench", port_offset=3).render())
    assert current_port_offset(toml_path) == 3


def test_current_port_offset_zero_when_file_missing(tmp_path: Path) -> None:
    assert current_port_offset(tmp_path / "bench.toml") == 0


def test_current_port_offset_zero_when_file_invalid(tmp_path: Path) -> None:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text("not valid toml {{{")
    assert current_port_offset(toml_path) == 0
