from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore


FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "bench_toml"


@pytest.mark.parametrize(
    ("filename", "name", "db_type", "process_manager"),
    [
        ("development_postgres.toml", "postgres-dev", "postgres", ""),
        ("production_systemd.toml", "systemd-prod", "mariadb", "systemd"),
        ("legacy_supervisor.toml", "legacy-supervisor", "mariadb", "supervisor"),
    ],
)
def test_representative_bench_toml_loads_and_round_trips(
    tmp_path: Path,
    filename: str,
    name: str,
    db_type: str,
    process_manager: str,
) -> None:
    config = BenchConfig.from_file(FIXTURES / filename)
    round_trip_path = tmp_path / "bench.toml"
    BenchTomlStore(round_trip_path).write(config)

    assert config.name == name
    assert config.db_type == db_type
    assert config.production.process_manager == process_manager
    assert BenchConfig.from_file(round_trip_path) == config
