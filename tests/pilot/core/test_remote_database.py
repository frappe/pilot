from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import MariaDBConfig
from pilot.core.database import make_database
from pilot.core.database.engines.remote_mariadb import RemoteMariaDB
from pilot.exceptions import DatabaseError
from tests.pilot.core.test_slow_log import LOG

VARIABLES = {
    "SELECT @@datadir": "/var/lib/mysql/",
    "SELECT @@log_output": "FILE",
    "SELECT @@slow_query_log_file": "/var/log/mysql/mariadb-slow.log",
    "SELECT @@log_error": "/var/log/mysql/error.log",
    "SELECT @@log_bin_index": "mysql-bin.index",
}


def _database(agent: MagicMock) -> RemoteMariaDB:
    database = RemoteMariaDB("fdaa::3", 3306, "pilot", "secret", "", agent=agent)
    database.get_scalar = VARIABLES.get  # type: ignore[method-assign]
    return database


def test_a_remote_config_gets_an_engine_that_reads_the_server_through_the_agent() -> None:
    mariadb = MariaDBConfig(host="fdaa::3", port=3306, remote=True, agent_token="token")
    config = SimpleNamespace(db_type="mariadb", mariadb=mariadb)

    database = make_database(config)  # type: ignore[arg-type]

    assert isinstance(database, RemoteMariaDB)
    assert (database.agent.base_url, database.agent.token) == ("http://[fdaa::3]:7781", "token")


def test_slow_queries_are_read_from_the_tail_of_the_remote_log() -> None:
    agent = MagicMock()
    agent.get_file.return_value = {"exists": True, "size_bytes": len(LOG), "text": LOG}
    database = _database(agent)

    with patch.object(RemoteMariaDB, "get_global_variables", return_value={"slow_query_log": "ON"}):
        report = database.get_slow_queries()

    assert report.log_path == "/var/log/mysql/mariadb-slow.log"
    assert [query.database for query in report.queries] == ["site_one", "site_two"]
    path, tail_bytes = agent.get_file.call_args.args[0], agent.get_file.call_args.kwargs["tail_bytes"]
    assert path == "/var/log/mysql/mariadb-slow.log" and tail_bytes > 0


def test_a_missing_remote_log_reports_no_file() -> None:
    agent = MagicMock()
    agent.get_file.return_value = {"exists": False, "size_bytes": 0, "text": ""}

    assert _database(agent).get_error_log_file() is None


@pytest.mark.parametrize(
    ("answer", "size_bytes"),
    [
        pytest.param({"exists": True, "size_bytes": 4096, "text": ""}, 4096, id="sized"),
        pytest.param(DatabaseError("refused"), 0, id="refused"),
    ],
)
def test_relative_files_are_sized_inside_the_remote_data_directory(answer: object, size_bytes: int) -> None:
    agent = MagicMock()
    agent.get_file.side_effect = [answer]

    assert _database(agent)._variable_file_size("@@log_bin_index") == size_bytes
    agent.get_file.assert_called_once_with("/var/lib/mysql/mysql-bin.index")


@pytest.mark.parametrize(
    ("answer", "free_bytes"),
    [
        pytest.param(10 * 1024**3, 10 * 1024**3, id="reachable"),
        pytest.param(DatabaseError("unreachable"), None, id="unreachable"),
    ],
)
def test_free_disk_comes_from_the_agent(answer: object, free_bytes: int | None) -> None:
    agent = MagicMock()
    agent.get_free_disk_bytes.side_effect = [answer]

    assert _database(agent).get_free_disk_bytes() == free_bytes


def test_local_files_still_resolve_against_the_data_directory(tmp_path: Path) -> None:
    from pilot.core.database.engines.mariadb import MariaDB

    (tmp_path / "mysql-bin.index").write_text("x" * 7)
    database = MariaDB("localhost", 3306, "root", "", "")
    database.get_scalar = {"SELECT @@log_bin_index": "mysql-bin.index", "SELECT @@datadir": str(tmp_path)}.get  # type: ignore[method-assign]

    assert database._variable_file_size("@@log_bin_index") == 7
