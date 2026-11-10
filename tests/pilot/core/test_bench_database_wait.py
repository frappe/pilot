from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench.runtime import BenchRuntime
from pilot.managers.database import MariaDBManager
from tests.pilot.commands.test_commands import make_bench


def _runtime(tmp_path: Path, production: bool = True, existing: bool = False) -> BenchRuntime:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    bench.config.mariadb.existing = existing
    return BenchRuntime(bench)


@pytest.mark.parametrize("reachable", [True, False])
def test_a_production_bench_is_ready_when_its_own_database_is(tmp_path: Path, reachable: bool) -> None:
    with patch.object(MariaDBManager, "is_reachable", return_value=reachable):
        assert _runtime(tmp_path).is_database_ready is reachable


@pytest.mark.parametrize("production, existing", [(False, False), (True, True)])
def test_a_development_bench_or_an_existing_server_is_always_ready(
    tmp_path: Path, production: bool, existing: bool
) -> None:
    with patch.object(MariaDBManager, "is_reachable") as is_reachable:
        assert _runtime(tmp_path, production, existing).is_database_ready

    is_reachable.assert_not_called()


def test_the_address_of_a_managed_mariadb_is_its_host_and_port(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.bench.config.mariadb.host, runtime.bench.config.mariadb.port = "localhost", 3310

    assert runtime.get_database_address() == "localhost:3310"


def test_the_address_of_a_managed_postgres_is_its_host_and_port(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.bench.config.db_type = "postgres"
    runtime.bench.config.postgres.host, runtime.bench.config.postgres.port = "localhost", 5450

    assert runtime.get_database_address() == "localhost:5450"


@pytest.mark.parametrize("production, existing, db_type", [(False, False, "mariadb"), (True, True, "mariadb"), (True, False, "sqlite")])
def test_there_is_no_address_to_wait_for(tmp_path: Path, production: bool, existing: bool, db_type: str) -> None:
    runtime = _runtime(tmp_path, production, existing)
    runtime.bench.config.db_type = db_type

    assert runtime.get_database_address() == ""


def test_the_web_unit_waits_with_the_address_and_never_loads_bench_toml(tmp_path: Path) -> None:
    from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder

    runtime = _runtime(tmp_path)
    web = ProcessDefinition("web", ["/bin/web"], tmp_path / "web.log")

    ProcessDefinitionBuilder(runtime.bench, Path("/env/bin/python"), False).wait_for_database_before_web([web])

    assert web.pre_run[-2].endswith("pilot/internal/wait_for_socket.py")
    assert web.pre_run[-1] == runtime.get_database_address()
    assert "-b" not in web.pre_run and "wait-for-database" not in web.pre_run


def test_a_sqlite_bench_adds_no_wait_to_the_web_unit(tmp_path: Path) -> None:
    from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder

    runtime = _runtime(tmp_path)
    runtime.bench.config.db_type = "sqlite"
    web = ProcessDefinition("web", ["/bin/web"], tmp_path / "web.log")

    ProcessDefinitionBuilder(runtime.bench, Path("/env/bin/python"), False).wait_for_database_before_web([web])

    assert web.pre_run == []
