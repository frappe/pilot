from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.database import MariaDBManager, PostgresManager
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.patches.use_notify_for_database_units import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
db_type = "{db_type}"

[production]
enabled = {production}
process_manager = "{manager}"

[admin]
domain = "admin-{name}.example.com"
"""
_UNIT = "[Service]\nType=simple\nExecStart=/bin/db\n"


def _bench(root: Path, name: str, db_type: str = "mariadb", manager: str = "") -> None:
    """A development bench, or a production bench when it has a process manager."""
    (root / name).mkdir(parents=True)
    production = str(bool(manager)).lower()
    (root / name / "bench.toml").write_text(
        _TOML.format(name=name, db_type=db_type, production=production, manager=manager)
    )


@pytest.fixture(autouse=True)
def actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the patch asks systemd to do, in order. The database units are files in tmp_path."""
    actions: list[str] = []
    monkeypatch.setattr("pilot.managers.platform.HostPlatform.is_macos", False)
    monkeypatch.setattr(
        "pilot.managers.database.base.run_command",
        lambda command, **kwargs: actions.append("database-reload"),
    )
    for database in (MariaDBManager, PostgresManager):
        monkeypatch.setattr(database, "unit_path", property(lambda self: tmp_path / self._UNIT_NAME))
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: True)
    monkeypatch.setattr(
        SystemdProcessManager, "write_config", lambda self: actions.append(f"units:{self.bench.config.name}")
    )
    monkeypatch.setattr(
        SystemdProcessManager, "reload_manager_config", lambda self: actions.append("units-reload")
    )
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


def test_a_systemd_bench_gets_its_units_rewritten_after_the_database_unit(
    tmp_path: Path, actions: list[str]
) -> None:
    """Its web unit ran the removed `wait-for-database` command before it started."""
    (tmp_path / "pilot-mariadb.service").write_text(_UNIT)
    _bench(tmp_path / "benches", "one", manager="systemd")

    run(tmp_path / "benches")

    assert actions == ["database-reload", "units:one", "units-reload"]


def test_a_systemd_bench_without_a_managed_database_gets_its_units_rewritten_too(
    tmp_path: Path, actions: list[str]
) -> None:
    _bench(tmp_path / "benches", "one", "sqlite", manager="systemd")

    run(tmp_path / "benches")

    assert actions == ["units:one", "units-reload"]


def test_the_patch_runs_once_for_each_bench(tmp_path: Path, actions: list[str]) -> None:
    (tmp_path / "pilot-mariadb.service").write_text(_UNIT)
    _bench(tmp_path / "benches", "one", manager="systemd")

    run(tmp_path / "benches")
    run(tmp_path / "benches")

    assert actions == ["database-reload", "units:one", "units-reload"]


def test_development_and_supervisor_benches_keep_their_process_config(
    tmp_path: Path, actions: list[str]
) -> None:
    _bench(tmp_path / "benches", "dev")
    _bench(tmp_path / "benches", "super", manager="supervisor")

    run(tmp_path / "benches")

    assert actions == []


def test_a_bench_that_is_not_set_up_in_systemd_is_marked_done_without_a_rewrite(
    tmp_path: Path, actions: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: False)
    _bench(tmp_path / "benches", "one", manager="systemd")

    run(tmp_path / "benches")
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: True)
    run(tmp_path / "benches")

    assert actions == []
