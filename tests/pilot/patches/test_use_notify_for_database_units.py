from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.database import MariaDBManager, PostgresManager
from pilot.patches.use_notify_for_database_units import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
db_type = "{db_type}"
"""
_UNIT = "[Service]\nType=simple\nExecStart=/bin/db\n"


def _bench(root: Path, name: str, db_type: str = "mariadb") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, db_type=db_type))


@pytest.fixture(autouse=True)
def actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the patch asks systemd to do, in order. The database units are files in tmp_path."""
    actions: list[str] = []
    monkeypatch.setattr("pilot.managers.database.base.is_macos", lambda: False)
    monkeypatch.setattr(
        "pilot.managers.database.base.run_command",
        lambda command, **kwargs: actions.append("database-reload"),
    )
    for database in (MariaDBManager, PostgresManager):
        monkeypatch.setattr(database, "unit_path", property(lambda self: tmp_path / self._UNIT_NAME))
    return actions


def test_an_existing_database_unit_reports_ready(tmp_path: Path, actions: list[str]) -> None:
    unit = tmp_path / "pilot-mariadb.service"
    unit.write_text(_UNIT)
    _bench(tmp_path / "benches", "one")

    run(tmp_path / "benches")

    assert unit.read_text() == "[Service]\nType=notify\nTimeoutStartSec=15min\nExecStart=/bin/db\n"
    assert actions == ["database-reload"]


def test_a_postgres_unit_is_rewritten_too(tmp_path: Path) -> None:
    unit = tmp_path / "pilot-postgres.service"
    unit.write_text(_UNIT)
    _bench(tmp_path / "benches", "one", "postgres")

    run(tmp_path / "benches")

    assert "Type=notify\nTimeoutStartSec=infinity\n" in unit.read_text()


def test_a_missing_unit_is_left_alone(tmp_path: Path, actions: list[str]) -> None:
    _bench(tmp_path / "benches", "one")

    run(tmp_path / "benches")

    assert actions == []


def test_the_patch_runs_once_for_each_bench(tmp_path: Path, actions: list[str]) -> None:
    (tmp_path / "pilot-mariadb.service").write_text(_UNIT)
    _bench(tmp_path / "benches", "one")

    run(tmp_path / "benches")
    run(tmp_path / "benches")

    assert actions == ["database-reload"]
