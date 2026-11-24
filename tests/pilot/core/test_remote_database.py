from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def test_relative_files_are_sized_inside_the_remote_data_directory() -> None:
    agent = MagicMock()
    agent.get_file.return_value = {"exists": True, "size_bytes": 4096, "text": ""}

    assert _database(agent)._variable_file_size("@@log_bin_index") == 4096
    agent.get_file.assert_called_once_with("/var/lib/mysql/mysql-bin.index")


def test_a_file_the_agent_refuses_has_no_size() -> None:
    agent = MagicMock()
    agent.get_file.side_effect = DatabaseError("refused")

    assert _database(agent)._variable_file_size("@@log_error") == 0


def test_free_disk_comes_from_the_agent() -> None:
    agent = MagicMock()
    agent.get_free_disk_bytes.return_value = 10 * 1024**3

    assert _database(agent).get_free_disk_bytes() == 10 * 1024**3


def test_free_disk_is_unknown_when_the_agent_cannot_be_reached() -> None:
    agent = MagicMock()
    agent.get_free_disk_bytes.side_effect = DatabaseError("unreachable")

    assert _database(agent).get_free_disk_bytes() is None


def test_local_files_still_resolve_against_the_data_directory(tmp_path: Path) -> None:
    from pilot.core.database.engines.mariadb import MariaDB

    (tmp_path / "mysql-bin.index").write_text("x" * 7)
    database = MariaDB("localhost", 3306, "root", "", "")
    database.get_scalar = {"SELECT @@log_bin_index": "mysql-bin.index", "SELECT @@datadir": str(tmp_path)}.get  # type: ignore[method-assign]

    assert database._variable_file_size("@@log_bin_index") == 7
