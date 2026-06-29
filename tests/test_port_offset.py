"""Port-offset handling. Ports are deliberately not wizard-editable (kept out of
FLAT_KEYS) so a new bench gets an auto-picked, collision-free offset; these tests
guard both that exclusion and the offset application/read-back."""

from __future__ import annotations

from pathlib import Path

from pilot.config.serializer import default_ports
from pilot.config.toml_store import BenchTomlStore


def test_default_ports_returns_all_fields() -> None:
    assert set(default_ports()) == {
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


def _write(tmp_path: Path, settings: dict | None = None, port_offset: int = 0) -> dict:
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("my-bench", settings or {}, port_offset=port_offset)
    return store.read_raw()


def test_port_offset_zero_leaves_defaults(tmp_path: Path) -> None:
    data = _write(tmp_path)
    assert data["bench"]["http_port"] == 8000
    assert data["admin"]["port"] == 7000


def test_port_offset_shifts_all_fields_together(tmp_path: Path) -> None:
    data = _write(tmp_path, port_offset=1)
    assert data["bench"]["http_port"] == 8001
    assert data["bench"]["socketio_port"] == 9001
    assert data["redis"]["cache_port"] == 13001
    assert data["redis"]["queue_port"] == 11001
    assert data["admin"]["port"] == 7001
    assert data["mariadb"]["port"] == 3307


def test_port_fields_not_settable_via_settings(tmp_path: Path) -> None:
    """Regression: settings can't touch any port field — only port_offset can —
    so carrying a current value forward (as the wizard save does) can't double-offset."""
    data = _write(tmp_path, settings={"admin_port": 9999, "http_port": 1234}, port_offset=1)
    assert data["bench"]["http_port"] == 8001
    assert data["admin"]["port"] == 7001


def test_port_offset_reads_back(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("my-bench", {}, port_offset=3)
    assert store.port_offset() == 3


def test_port_offset_zero_when_file_missing(tmp_path: Path) -> None:
    assert BenchTomlStore.for_bench(tmp_path).port_offset() == 0


def test_port_offset_zero_when_file_invalid(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text("not valid toml {{{")
    assert BenchTomlStore.for_bench(tmp_path).port_offset() == 0
