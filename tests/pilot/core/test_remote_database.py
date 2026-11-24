from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pilot.config import MariaDBConfig
from pilot.core.database import make_database
from pilot.core.database.engines.remote_mariadb import RemoteMariaDB
from pilot.exceptions import CommandError
from tests.pilot.core.test_slow_log import LOG

VARIABLES = {
    "SELECT @@datadir": "/var/lib/mysql/",
    "SELECT @@log_output": "FILE",
    "SELECT @@slow_query_log_file": "/var/log/mysql/mariadb-slow.log",
    "SELECT @@log_error": "/var/log/mysql/error.log",
    "SELECT @@log_bin_index": "mysql-bin.index",
}


def _database(ssh_host: MagicMock) -> RemoteMariaDB:
    database = RemoteMariaDB("fdaa::3", 3306, "pilot", "secret", "", ssh_host=ssh_host)
    database.get_scalar = VARIABLES.get  # type: ignore[method-assign]
    return database


def test_a_remote_config_gets_an_engine_that_reads_the_host_over_ssh() -> None:
    mariadb = MariaDBConfig(
        host="fdaa::3",
        port=3306,
        remote=True,
        ssh_user="ubuntu",
        ssh_key_path="/home/pilot/.ssh/db",
        memory_mb=4096,
    )
    config = SimpleNamespace(db_type="mariadb", mariadb=mariadb)

    database = make_database(config)  # type: ignore[arg-type]

    assert isinstance(database, RemoteMariaDB)
    assert (database.ssh_host.address, database.ssh_host.user) == ("fdaa::3", "ubuntu")


def test_slow_queries_are_read_from_the_tail_of_the_remote_log() -> None:
    ssh_host = MagicMock()
    ssh_host.exists.return_value = True
    ssh_host.run.return_value = LOG
    database = _database(ssh_host)

    with patch.object(RemoteMariaDB, "get_global_variables", return_value={"slow_query_log": "ON"}):
        report = database.get_slow_queries()

    assert report.log_path == "/var/log/mysql/mariadb-slow.log"
    assert [query.database for query in report.queries] == ["site_one", "site_two"]
    ssh_host.exists.assert_called_with("/var/log/mysql/mariadb-slow.log")
    assert ssh_host.run.call_args.args[0][:2] == ["tail", "-c"]


def test_a_missing_remote_log_reports_no_file() -> None:
    ssh_host = MagicMock()
    ssh_host.exists.return_value = False

    assert _database(ssh_host).get_error_log_file() is None


def test_relative_files_are_sized_inside_the_remote_data_directory() -> None:
    ssh_host = MagicMock()
    ssh_host.run.return_value = "4096\n"

    assert _database(ssh_host)._variable_file_size("@@log_bin_index") == 4096
    ssh_host.run.assert_called_once_with(["stat", "-c", "%s", "/var/lib/mysql/mysql-bin.index"])


def test_an_unreadable_remote_file_has_no_size() -> None:
    ssh_host = MagicMock()
    ssh_host.run.side_effect = CommandError("missing", returncode=1)

    assert _database(ssh_host)._variable_file_size("@@log_error") == 0


def test_free_disk_is_measured_on_the_remote_data_directory() -> None:
    ssh_host = MagicMock()
    ssh_host.get_free_disk_bytes.return_value = 10 * 1024**3

    assert _database(ssh_host).get_free_disk_bytes() == 10 * 1024**3
    ssh_host.get_free_disk_bytes.assert_called_once_with("/var/lib/mysql/")


def test_free_disk_is_unknown_when_the_host_cannot_be_reached() -> None:
    ssh_host = MagicMock()
    ssh_host.get_free_disk_bytes.side_effect = CommandError("refused", returncode=255)

    assert _database(ssh_host).get_free_disk_bytes() is None


def test_local_files_still_resolve_against_the_data_directory(tmp_path: Path) -> None:
    from pilot.core.database.engines.mariadb import MariaDB

    (tmp_path / "mysql-bin.index").write_text("x" * 7)
    database = MariaDB("localhost", 3306, "root", "", "")
    database.get_scalar = {"SELECT @@log_bin_index": "mysql-bin.index", "SELECT @@datadir": str(tmp_path)}.get  # type: ignore[method-assign]

    assert database._variable_file_size("@@log_bin_index") == 7
