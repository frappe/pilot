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


def _bench(root: Path, name: str, db_type: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, db_type=db_type))


@pytest.fixture
def reloads(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    reloads: list[list[str]] = []
    monkeypatch.setattr("pilot.managers.database.base.is_macos", lambda: False)
    monkeypatch.setattr("pilot.managers.database.base.run_command", lambda command, **kwargs: reloads.append(command))
    return reloads


def _unit_file(monkeypatch: pytest.MonkeyPatch, manager: type, path: Path) -> None:
    monkeypatch.setattr(manager, "unit_path", property(lambda self: path))


def test_an_existing_database_unit_reports_ready(tmp_path: Path, monkeypatch, reloads) -> None:
    unit = tmp_path / "pilot-mariadb.service"
    unit.write_text(_UNIT)
    _unit_file(monkeypatch, MariaDBManager, unit)
    _bench(tmp_path / "benches", "one", "mariadb")

    run(tmp_path / "benches")

    assert unit.read_text() == "[Service]\nType=notify\nTimeoutStartSec=15min\nExecStart=/bin/db\n"
    assert len(reloads) == 1


def test_a_postgres_unit_is_rewritten_too(tmp_path: Path, monkeypatch, reloads) -> None:
    unit = tmp_path / "pilot-postgres.service"
    unit.write_text(_UNIT)
    _unit_file(monkeypatch, PostgresManager, unit)
    _bench(tmp_path / "benches", "one", "postgres")

    run(tmp_path / "benches")

    assert "Type=notify\nTimeoutStartSec=infinity\n" in unit.read_text()


def test_the_patch_runs_once_and_skips_a_unit_that_already_reports_ready(
    tmp_path: Path, monkeypatch, reloads
) -> None:
    unit = tmp_path / "pilot-mariadb.service"
    unit.write_text(_UNIT)
    _unit_file(monkeypatch, MariaDBManager, unit)
    _bench(tmp_path / "benches", "one", "mariadb")

    run(tmp_path / "benches")
    run(tmp_path / "benches")

    assert len(reloads) == 1


def test_a_missing_unit_is_left_alone(tmp_path: Path, monkeypatch, reloads) -> None:
    _unit_file(monkeypatch, MariaDBManager, tmp_path / "missing.service")
    _bench(tmp_path / "benches", "one", "mariadb")

    run(tmp_path / "benches")

    assert reloads == []
