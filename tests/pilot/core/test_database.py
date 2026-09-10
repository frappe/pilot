"""Tests for pilot.core.database - SQLite is tested live; MariaDB/PostgreSQL use mocks."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig, MariaDBConfig, PostgresConfig, RedisConfig, WorkerConfig, WorkerGroup
from pilot.core.database import (
    DatabaseProcess,
    MariaDB,
    PostgreSQL,
    SQLite,
    make_database,
    make_site_database,
)
from pilot.exceptions import DatabaseError


def _bench_config(db_type: str = "mariadb") -> BenchConfig:
    return BenchConfig(
        name="test-bench",
        python_version="3.14",
        db_type=db_type,
        mariadb=MariaDBConfig(root_password="secret"),
        postgres=PostgresConfig(root_password="pgpw"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )


def test_sqlite_execute_select(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    db = SQLite(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE foo (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO foo VALUES (1, 'alice'), (2, 'bob')")
    conn.commit()
    conn.close()

    result = db.execute("SELECT * FROM foo ORDER BY id")
    assert result.columns == ["id", "name"]
    assert result.rows == [[1, "alice"], [2, "bob"]]
    assert result.truncated is False
    assert result.duration_ms >= 0


def test_sqlite_execute_read_only_does_not_persist(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    db = SQLite(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    conn.close()

    db.execute("INSERT INTO t VALUES (42)", read_only=False)
    result = db.execute("SELECT * FROM t")
    assert result.rows == [[42]]


def test_sqlite_execute_empty_result(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    db = SQLite(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE empty_t (id INTEGER)")
    conn.commit()
    conn.close()

    result = db.execute("SELECT * FROM empty_t")
    assert result.columns == ["id"]
    assert result.rows == []


def test_sqlite_get_tables(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE alpha (x INTEGER)")
    conn.execute("CREATE TABLE beta (y TEXT)")
    conn.commit()
    conn.close()

    tables = SQLite(db_path).get_tables()
    assert "alpha" in tables
    assert "beta" in tables


def test_sqlite_get_table_columns(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.commit()
    conn.close()

    cols = SQLite(db_path).get_table_columns("person")
    names = [c["name"] for c in cols]
    assert "id" in names
    assert "name" in names


def test_sqlite_get_schema(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE doc (id INTEGER, body TEXT)")
    conn.commit()
    conn.close()

    schema = SQLite(db_path).get_schema()
    assert len(schema) == 1
    assert schema[0]["name"] == "doc"
    assert any(c["name"] == "id" for c in schema[0]["columns"])


def test_sqlite_get_schema_groups_columns_per_table_in_one_connection(tmp_path: Path) -> None:
    # get_schema() used to open one connection per table (via get_table_columns);
    # a real Frappe site has hundreds of tables, so that meant hundreds of
    # connections per schema fetch. Assert it's back down to a single connection,
    # and that columns still land under the right table.
    db_path = str(tmp_path / "test.db")
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE alpha (id INTEGER, name TEXT)")
    conn.execute("CREATE TABLE beta (id INTEGER, amount REAL)")
    conn.commit()
    conn.close()

    db = SQLite(db_path)
    with patch.object(SQLite, "_connect", wraps=db._connect) as spy:
        schema = db.get_schema()
        assert spy.call_count == 1

    by_name = {t["name"]: t["columns"] for t in schema}
    assert {c["name"] for c in by_name["alpha"]} == {"id", "name"}
    assert {c["name"] for c in by_name["beta"]} == {"id", "amount"}


def test_sqlite_execute_raises_on_bad_query(tmp_path: Path) -> None:
    db = SQLite(str(tmp_path / "x.db"))
    with pytest.raises(DatabaseError):
        db.execute("SELECT * FROM nonexistent_table")


def test_sqlite_read_only_blocks_ddl(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    db = SQLite(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE keep_me (id INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseError):
        db.execute("DROP TABLE keep_me", read_only=True)

    assert "keep_me" in db.get_tables()


def test_sqlite_read_only_blocks_dml(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test.db")
    db = SQLite(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseError):
        db.execute("INSERT INTO t VALUES (2)", read_only=True)

    result = db.execute("SELECT * FROM t")
    assert result.rows == [[1]]


def test_mariadb_get_schema_uses_one_connection() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="d")
    fake_cursor = MagicMock()
    fake_cursor.fetchall.side_effect = [
        [{"Tables_in_d": "alpha"}, {"Tables_in_d": "beta"}],
        [
            {"tbl": "alpha", "col": "id", "typ": "int(11)"},
            {"tbl": "alpha", "col": "name", "typ": "varchar(140)"},
            {"tbl": "beta", "col": "id", "typ": "int(11)"},
        ],
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(MariaDB, "_connect", return_value=fake_conn) as connect:
        schema = db.get_schema()
        assert connect.call_count == 1

    by_name = {t["name"]: t["columns"] for t in schema}
    assert [c["name"] for c in by_name["alpha"]] == ["id", "name"]
    assert [c["name"] for c in by_name["beta"]] == ["id"]


def test_postgres_connect_failure_surfaces_as_a_database_error() -> None:
    """A raw driver error escaped _connect and reached the API as an opaque 500,
    hiding why diagnostics failed."""
    import sys
    import types

    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.Error = type("Error", (Exception,), {})
    fake_psycopg2.connect = MagicMock(side_effect=fake_psycopg2.Error("connection refused"))

    db = PostgreSQL(host="db.internal", port=5432, user="u", password="p", database="d")
    with (
        patch.dict(sys.modules, {"psycopg2": fake_psycopg2}),
        pytest.raises(DatabaseError, match=r"Could not connect to PostgreSQL at db\.internal:5432"),
    ):
        db.get_active_connections()


def test_postgres_get_schema_uses_one_connection() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    fake_cursor = MagicMock()
    fake_cursor.fetchall.side_effect = [
        [("alpha",), ("beta",)],
        [
            ("alpha", "id", "integer"),
            ("alpha", "name", "character varying"),
            ("beta", "id", "integer"),
        ],
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(PostgreSQL, "_connect", return_value=fake_conn) as connect:
        schema = db.get_schema()
        assert connect.call_count == 1

    by_name = {t["name"]: t["columns"] for t in schema}
    assert [c["name"] for c in by_name["alpha"]] == ["id", "name"]
    assert [c["name"] for c in by_name["beta"]] == ["id"]


def _canned_execute(responses: dict, calls: list | None = None):
    """Fake Database.execute keyed on a substring of the query."""

    def execute(self, query: str, read_only: bool = True) -> "QueryResult":
        from pilot.core.database import QueryResult

        if calls is not None:
            calls.append((query, read_only))
        for pattern, (columns, rows) in responses.items():
            if pattern in query:
                return QueryResult(columns=columns, rows=rows, duration_ms=0)
        raise AssertionError(f"Unexpected query: {query}")

    return execute


_MARIADB_PROCESS_COLUMNS = ["Id", "User", "Host", "db", "Command", "Time", "State", "Info"]


def test_mariadb_get_process_list_normalises_rows() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "PROCESSLIST": (
            _MARIADB_PROCESS_COLUMNS,
            [[7, "app", "localhost:53422", "mydb", "Query", 3, "Sending data", "SELECT 1"]],
        )
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        processes = db.get_process_list()

    assert processes == [
        DatabaseProcess(
            id=7,
            user="app",
            host="localhost:53422",
            database="mydb",
            command="Query",
            state="Sending data",
            duration_seconds=3.0,
            query="SELECT 1",
        )
    ]


def test_mariadb_get_process_list_filters_by_database() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "PROCESSLIST": (
            _MARIADB_PROCESS_COLUMNS,
            [
                [7, "app", "h:1", "site_one", "Query", 3, "Sending data", "SELECT 1"],
                [8, "app", "h:2", "site_two", "Query", 1, "Sending data", "SELECT 2"],
                [9, "root", "h:3", None, "Sleep", 0, "", None],
            ],
        )
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        assert [p.id for p in db.get_process_list("site_one")] == [7]
        # No database means server-wide, including connections with no database.
        assert [p.id for p in db.get_process_list()] == [7, 8, 9]
        # An idle connection reports no state of its own.
        assert db.get_process_list()[2].state is None


def test_mariadb_kill_process_issues_kill_connection() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    calls: list = []
    with patch.object(MariaDB, "execute", _canned_execute({"KILL": ([], [])}, calls)):
        db.kill_process(4096)

    assert calls == [("KILL CONNECTION 4096", False)]


@pytest.mark.parametrize("process_id", ["7; DROP TABLE x", 7.5, None, True, 0, -1])
def test_mariadb_kill_process_rejects_non_positive_integer_ids(process_id) -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    calls: list = []
    with (
        patch.object(MariaDB, "execute", _canned_execute({"KILL": ([], [])}, calls)),
        pytest.raises(DatabaseError, match="Process id"),
    ):
        db.kill_process(process_id)

    assert calls == []


def test_postgres_kill_process_terminates_backend() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    with patch.object(PostgreSQL, "execute", _canned_execute({"pg_terminate_backend": (["x"], [[True]])})):
        db.kill_process(4096)


def test_postgres_kill_process_raises_when_backend_is_gone() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    with (
        patch.object(PostgreSQL, "execute", _canned_execute({"pg_terminate_backend": (["x"], [[False]])})),
        pytest.raises(DatabaseError, match="No such process: 4096"),
    ):
        db.kill_process(4096)


def test_mariadb_get_active_connections_reads_threads_connected() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {"Threads_connected": (["Variable_name", "Value"], [["Threads_connected", "12"]])}
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        assert db.get_active_connections() == 12


def test_mariadb_get_lock_waits_combines_status_and_timeout() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "Innodb_row_lock_current_waits": (["Variable_name", "Value"], [["Innodb_row_lock_current_waits", "2"]]),
        "Innodb_row_lock_waits": (["Variable_name", "Value"], [["Innodb_row_lock_waits", "95"]]),
        "@@innodb_lock_wait_timeout": (["@@innodb_lock_wait_timeout"], [[50]]),
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        waits = db.get_lock_waits()

    assert waits.current_waits == 2
    assert waits.total_waits == 95
    assert waits.timeout_seconds == 50


def test_mariadb_get_lock_waits_raises_on_unknown_status_variable() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {"SHOW GLOBAL STATUS": (["Variable_name", "Value"], [])}
    with patch.object(MariaDB, "execute", _canned_execute(responses)), pytest.raises(DatabaseError, match="status"):
        db.get_lock_waits()


def test_mariadb_get_lock_wait_rows_maps_joined_columns() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "INNODB_LOCK_WAITS": (
            ["requesting_trx_id", "lock_type", "lock_mode", "lock_table", "lock_index",
             "trx_state", "trx_started", "trx_query", "trx_rows_locked", "trx_rows_modified", "DB"],
            [[42, "RECORD", "X", "`db`.`tabDoc`", "PRIMARY", "LOCK WAIT",
              "2026-01-01 00:00:00", "UPDATE tabDoc SET x=1", 3, 1, "db"]],
        )
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        rows = db.get_lock_wait_rows()

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "42"
    assert row.type == "RECORD"
    assert row.mode == "X"
    assert row.table == "`db`.`tabDoc`"
    assert row.index == "PRIMARY"
    assert row.state == "LOCK WAIT"
    assert row.started == "2026-01-01 00:00:00"
    assert row.query == "UPDATE tabDoc SET x=1"
    assert row.rows_locked == 3
    assert row.rows_modified == 1


def test_mariadb_get_lock_wait_rows_filters_by_database() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "INNODB_LOCK_WAITS": (
            ["requesting_trx_id", "lock_type", "lock_mode", "lock_table", "lock_index",
             "trx_state", "trx_started", "trx_query", "trx_rows_locked", "trx_rows_modified", "DB"],
            [
                [42, "RECORD", "X", "t", "PRIMARY", "LOCK WAIT", None, None, 1, 0, "site_one"],
                [43, "RECORD", "X", "t", "PRIMARY", "LOCK WAIT", None, None, 1, 0, "site_two"],
            ],
        )
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        assert [r.id for r in db.get_lock_wait_rows("site_one")] == ["42"]
        assert [r.id for r in db.get_lock_wait_rows()] == ["42", "43"]


def test_mariadb_get_database_size_splits_data_index_and_claimable(tmp_path: Path) -> None:
    db = MariaDB(host="localhost", port=3306, user="u", password="p", database="site_one")
    responses = {
        "information_schema.TABLES": ([], [[8855552, 6696960, 6291456]]),
        "@@datadir": (["@@datadir"], [[str(tmp_path)]]),
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        size = db.get_database_size()

    assert size.data_bytes == 8855552
    assert size.index_bytes == 6696960
    assert size.claimable_bytes == 6291456
    assert size.free_bytes > 0  # tmp_path is a real local disk


def test_mariadb_schema_sizes_count_space_a_rebuild_would_reclaim() -> None:
    """A schema holds its freed pages until it is rebuilt, so the disk cannot
    hand that space to anything else - it belongs in the schema's size."""
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    calls: list = []
    responses = {"information_schema.TABLES": ([], [["site_one", 500], [None, 7]])}

    with patch.object(MariaDB, "execute", _canned_execute(responses, calls)):
        sizes = db.get_schema_sizes()

    assert sizes == {"site_one": 500}
    assert "data_length + index_length + data_free" in calls[0][0]


def test_postgres_schema_sizes_skip_template_databases() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    responses = {"pg_database_size": ([], [["site_one", 900], ["postgres", 60]])}

    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        assert db.get_schema_sizes() == {"site_one": 900, "postgres": 60}


def test_mariadb_size_scope_follows_the_connections_database() -> None:
    bound = MariaDB(host="h", port=3306, user="u", password="p", database="site_one")
    server_wide = MariaDB(host="h", port=3306, user="u", password="p", database="")

    # The database name is never interpolated into the query.
    assert bound._size_scope == "table_schema = DATABASE()"
    assert "site_one" not in bound._size_scope
    assert "information_schema" in server_wide._size_scope


def test_mariadb_free_space_is_none_for_a_remote_server() -> None:
    db = MariaDB(host="db.internal", port=3306, user="u", password="p", database="site_one")
    responses = {"information_schema.TABLES": ([], [[1, 2, 3]])}
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        # A local data directory path would be meaningless for a remote host.
        assert db.get_database_size().free_bytes is None


def test_mariadb_get_table_sizes_orders_largest_first() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="site_one")
    responses = {
        "information_schema.TABLES": ([], [["tabDocField", 800, 400, 100], ["tabVersion", 500, 200, 0]]),
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        tables = db.get_table_sizes()

    assert [t.name for t in tables] == ["tabDocField", "tabVersion"]
    assert tables[0].data_bytes == 800
    assert tables[0].index_bytes == 400
    assert tables[0].claimable_bytes == 100


def test_mariadb_get_binlog_status_disabled() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {"@@log_bin": (["@@log_bin"], [[0]])}
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        status = db.get_binlog_status()

    assert status.enabled is False
    assert status.file_count == 0
    assert status.size_bytes == 0


def _binlog_responses(tmp_path: Path) -> dict:
    return {
        "@@log_bin_basename": (["@@log_bin_basename"], [[str(tmp_path / "mysql-bin")]]),
        "@@log_bin": (["@@log_bin"], [[1]]),
        "SHOW BINARY LOGS": (
            ["Log_name", "File_size"],
            [["mysql-bin.000001", 1024], ["mysql-bin.000002", 2048]],
        ),
    }


def test_mariadb_get_binlog_status_sums_file_sizes(tmp_path: Path) -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    with patch.object(MariaDB, "execute", _canned_execute(_binlog_responses(tmp_path))):
        status = db.get_binlog_status()

    assert status.enabled is True
    assert status.file_count == 2
    assert status.size_bytes == 3072


def test_mariadb_get_binlog_files_stats_local_files(tmp_path: Path) -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    (tmp_path / "mysql-bin.000001").write_bytes(b"x")

    with patch.object(MariaDB, "execute", _canned_execute(_binlog_responses(tmp_path))):
        files = db.get_binlog_files()

    assert [f.name for f in files] == ["mysql-bin.000001", "mysql-bin.000002"]
    assert [f.size_bytes for f in files] == [1024, 2048]
    assert files[0].modified_ms is not None  # exists on disk
    assert files[1].modified_ms is None  # unreadable/missing -> best-effort None


def test_mariadb_get_binlog_files_empty_when_disabled() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {"@@log_bin": (["@@log_bin"], [[0]])}
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        assert db.get_binlog_files() == []


def test_mariadb_get_storage_components_measures_each_log(tmp_path: Path) -> None:
    """@@log_error is relative to datadir, @@slow_query_log_file absolute, and
    @@log_bin_index unset - all three resolve without special-casing."""
    db = MariaDB(host="localhost", port=3306, user="u", password="p", database="")
    (tmp_path / "mysql-bin.000001").write_bytes(b"x" * 1024)
    (tmp_path / "mariadb.err").write_bytes(b"x" * 777)
    (tmp_path / "slow.log").write_bytes(b"x" * 42)
    responses = _binlog_responses(tmp_path) | {
        "@@datadir": (["@@datadir"], [[str(tmp_path)]]),
        "@@log_error": (["@@log_error"], [["mariadb.err"]]),
        "@@slow_query_log_file": (["@@slow_query_log_file"], [[str(tmp_path / "slow.log")]]),
        "@@log_bin_index": (["@@log_bin_index"], [[""]]),
    }

    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        components = db.get_storage_components()

    assert {c.key: c.bytes for c in components} == {
        "binlog": 3072,
        "error_log": 777,
        "slow_log": 42,
        "binlog_index": 0,
    }
    assert [c.label for c in components] == [
        "binary log",
        "error log",
        "slow query log",
        "binary log index",
    ]


def test_mariadb_get_data_directory_is_none_for_a_remote_server() -> None:
    db = MariaDB(host="db.example.com", port=3306, user="u", password="p", database="")
    assert db.get_data_directory() is None


def test_postgres_get_storage_components_reports_wal_and_server_log() -> None:
    db = PostgreSQL(host="localhost", port=5432, user="u", password="p", database="d")
    responses = {
        "pg_ls_waldir": (["sum"], [[33554432]]),
        "SHOW logging_collector": (["logging_collector"], [["on"]]),
        "pg_ls_logdir": (["sum"], [[8192]]),
    }

    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        components = db.get_storage_components()

    assert [(c.key, c.label, c.bytes) for c in components] == [
        ("wal", "write-ahead log", 33554432),
        ("server_log", "server log", 8192),
    ]


def test_postgres_server_log_is_zero_without_the_logging_collector() -> None:
    """Logging to stderr leaves no log directory for pg_ls_logdir to size."""
    db = PostgreSQL(host="localhost", port=5432, user="u", password="p", database="d")
    responses = {
        "pg_ls_waldir": (["sum"], [[16777216]]),
        "SHOW logging_collector": (["logging_collector"], [["off"]]),
    }

    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        components = db.get_storage_components()

    assert {c.key: c.bytes for c in components} == {"wal": 16777216, "server_log": 0}


def test_postgres_get_data_directory_is_none_for_a_remote_server() -> None:
    db = PostgreSQL(host="db.example.com", port=5432, user="u", password="p", database="d")
    assert db.get_data_directory() is None


def test_mariadb_purge_binlogs_issues_purge_for_known_file(tmp_path: Path) -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    calls: list = []
    responses = _binlog_responses(tmp_path)
    responses["PURGE BINARY LOGS"] = ([], [])
    with patch.object(MariaDB, "execute", _canned_execute(responses, calls)):
        db.purge_binlogs("mysql-bin.000002")

    purge_calls = [(q, ro) for q, ro in calls if "PURGE" in q]
    assert purge_calls == [("PURGE BINARY LOGS TO 'mysql-bin.000002'", False)]


def test_mariadb_purge_binlogs_rejects_unknown_file(tmp_path: Path) -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    with (
        patch.object(MariaDB, "execute", _canned_execute(_binlog_responses(tmp_path))),
        pytest.raises(DatabaseError, match="Unknown binlog"),
    ):
        db.purge_binlogs("mysql-bin.999999'; DROP TABLE x")


def test_postgres_get_active_connections_counts_pg_stat_activity() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    responses = {"pg_stat_activity": (["count"], [[4]])}
    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        assert db.get_active_connections() == 4


def test_postgres_get_lock_waits_treats_zero_timeout_as_disabled() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    responses = {
        "pg_locks": (["count"], [[1]]),
        "pg_settings": (["setting"], [["0"]]),
    }
    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        waits = db.get_lock_waits()

    assert waits.current_waits == 1
    assert waits.total_waits is None
    assert waits.timeout_seconds is None


def test_postgres_get_lock_wait_rows_leaves_unsupported_fields_none() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    responses = {
        "blocked.granted": (
            ["pid", "locktype", "mode", "relation", "state", "query_start", "query", "datname"],
            [
                [
                    123,
                    "relation",
                    "RowExclusiveLock",
                    16385,
                    "active",
                    "2026-01-01 00:00:00",
                    "UPDATE tabDoc SET x=1",
                    "site_one",
                ]
            ],
        ),
        "n.nspname": (["oid", "nspname", "relname"], [[16385, "public", "tabDoc"]]),
    }
    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        rows = db.get_lock_wait_rows("site_one")

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "123"
    assert row.type == "relation"
    assert row.mode == "RowExclusiveLock"
    assert row.table == "site_one.public.tabDoc"
    assert row.index is None
    assert row.state == "active"
    assert row.started == "2026-01-01 00:00:00"
    assert row.query == "UPDATE tabDoc SET x=1"
    assert row.rows_locked is None
    assert row.rows_modified is None


def test_postgres_server_wide_lock_waits_do_not_query_every_database() -> None:
    """The server-wide view refreshes every two seconds, so relation lookup
    must not open one connection for every database with a wait."""
    from pilot.core.database import QueryResult

    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    locks = [
        [1, "relation", "RowExclusiveLock", 16385, "active", None, "UPDATE", "site_one"],
        [2, "relation", "RowExclusiveLock", 16385, "active", None, "UPDATE", "site_two"],
        [3, "advisory", "ExclusiveLock", None, "active", None, "SELECT", "site_one"],
    ]

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        assert "blocked.granted" in query
        return QueryResult(columns=[], rows=locks, duration_ms=0)

    with patch.object(PostgreSQL, "execute", execute):
        rows = db.get_lock_wait_rows()

    assert [row.table for row in rows] == [
        "site_one (relation OID 16385)",
        "site_two (relation OID 16385)",
        None,
    ]


def test_postgres_site_lock_wait_includes_schema_in_relation_name() -> None:
    """Relation names need the database and schema because PostgreSQL permits
    the same table name in multiple schemas."""
    from pilot.core.database import QueryResult

    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    lock = [1, "relation", "RowExclusiveLock", 16385, "active", None, "UPDATE", "site_one"]
    asked: list[str] = []

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        if "blocked.granted" in query:
            return QueryResult(columns=[], rows=[lock], duration_ms=0)
        assert "JOIN pg_namespace" in query
        asked.append(self._database)
        return QueryResult(columns=[], rows=[[16385, "archive", "tabItem"]], duration_ms=0)

    with patch.object(PostgreSQL, "execute", execute):
        rows = db.get_lock_wait_rows("site_one")

    assert rows[0].table == "site_one.archive.tabItem"
    assert asked == ["site_one"]


def test_postgres_lock_waits_ask_no_catalog_when_nothing_is_waiting() -> None:
    """Locks auto-refresh every couple of seconds, so the common empty case
    must not open a connection per database."""
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    with patch.object(PostgreSQL, "execute", _canned_execute({"blocked.granted": ([], [])})):
        assert db.get_lock_wait_rows() == []


def test_postgres_diagnostics_filter_by_database() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    lock_rows = (
        ["pid", "locktype", "mode", "relation", "state", "query_start", "query", "datname"],
        [
            [1, "advisory", "ExclusiveLock", None, "active", None, None, "site_one"],
            [2, "advisory", "ExclusiveLock", None, "active", None, None, "site_two"],
        ],
    )
    with patch.object(PostgreSQL, "execute", _canned_execute({"blocked.granted": lock_rows})):
        assert [r.id for r in db.get_lock_wait_rows("site_one")] == ["1"]
    with patch.object(PostgreSQL, "execute", _canned_execute({"pg_stat_activity": _POSTGRES_PROCESSES})):
        assert [p.id for p in db.get_process_list("site_two")] == [2]


_POSTGRES_PROCESSES = (
    ["pid", "usename", "client_addr", "client_port", "datname", "state", "wait_event", "seconds", "query"],
    [
        [1, "app", "10.0.0.4", 53422, "site_one", "active", None, 3.4, "SELECT 1"],
        [2, "app", None, -1, "site_two", "idle", "ClientRead", 12.0, "SELECT 2"],
    ],
)


def test_postgres_get_process_list_normalises_rows() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    with patch.object(PostgreSQL, "execute", _canned_execute({"pg_stat_activity": _POSTGRES_PROCESSES})):
        processes = db.get_process_list()

    assert processes[0] == DatabaseProcess(
        id=1,
        user="app",
        host="10.0.0.4:53422",
        database="site_one",
        command="active",
        state=None,
        duration_seconds=3.4,
        query="SELECT 1",
    )
    # A connection over the local socket has no client address to report.
    assert processes[1].host is None


def test_both_engines_report_the_same_process_shape() -> None:
    """The dashboard reads one shape, so neither engine's own column names may
    reach it."""
    postgres = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    mariadb = MariaDB(host="h", port=3306, user="u", password="p", database="")
    mariadb_rows = {"PROCESSLIST": (_MARIADB_PROCESS_COLUMNS, [[7, "app", "h:1", "db", "Query", 3, "", None]])}

    with patch.object(PostgreSQL, "execute", _canned_execute({"pg_stat_activity": _POSTGRES_PROCESSES})):
        from_postgres = postgres.get_process_list()[0]
    with patch.object(MariaDB, "execute", _canned_execute(mariadb_rows)):
        from_mariadb = mariadb.get_process_list()[0]

    assert asdict(from_postgres).keys() == asdict(from_mariadb).keys()


def test_postgres_get_database_size_leaves_claimable_space_unknown() -> None:
    db = PostgreSQL(host="db.internal", port=5432, user="u", password="p", database="site_one")
    responses = {"pg_table_size": ([], [[8897315, 4096]])}
    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        size = db.get_database_size()

    assert size.data_bytes == 8897315
    assert size.index_bytes == 4096
    # Reclaimable bloat needs the pgstattuple extension; remote host has no readable datadir.
    assert size.claimable_bytes is None
    assert size.free_bytes is None


def test_postgres_server_wide_size_uses_one_combined_query() -> None:
    """A host may have hundreds of databases, so server scope must not open a
    connection and query pg_class once for every site."""
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    calls: list = []
    responses = {"pg_database_size": (["total", "unavailable"], [[2400, 0]])}
    with patch.object(PostgreSQL, "execute", _canned_execute(responses, calls)):
        size = db.get_database_size()

    assert size.data_bytes is None
    assert size.index_bytes is None
    assert size.total_bytes == 2400
    assert len(calls) == 1
    assert "datallowconn" not in calls[0][0]


def test_postgres_server_wide_size_fails_if_a_database_cannot_be_measured() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="")
    responses = {"pg_database_size": (["total", "unavailable"], [[2400, 1]])}

    with (
        patch.object(PostgreSQL, "execute", _canned_execute(responses)),
        pytest.raises(DatabaseError, match="1 database"),
    ):
        db.get_database_size()


def test_postgres_get_table_sizes_reports_per_relation() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="site_one")
    responses = {"c.relname": ([], [["tabDocField", 800, 400]])}
    with patch.object(PostgreSQL, "execute", _canned_execute(responses)):
        tables = db.get_table_sizes()

    assert [(t.name, t.data_bytes, t.index_bytes, t.claimable_bytes) for t in tables] == [
        ("tabDocField", 800, 400, None)
    ]


def test_postgres_get_binlog_status_falls_back_to_not_implemented() -> None:
    # PostgreSQL doesn't override the binlog trio - it inherits Database's default.
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    with pytest.raises(NotImplementedError):
        db.get_binlog_status()


def test_sqlite_server_diagnostics_fall_back_to_not_implemented(tmp_path: Path) -> None:
    # SQLite has no server - it inherits every server-only op from Database's default.
    db = SQLite(str(tmp_path / "x.db"))
    with pytest.raises(NotImplementedError):
        db.get_process_list()
    with pytest.raises(NotImplementedError):
        db.kill_process(1)
    with pytest.raises(NotImplementedError):
        db.get_active_connections()
    with pytest.raises(NotImplementedError):
        db.get_lock_waits()
    with pytest.raises(NotImplementedError):
        db.get_binlog_status()


def test_make_database_returns_mariadb_for_mariadb_bench() -> None:
    db = make_database(_bench_config("mariadb"))
    assert isinstance(db, MariaDB)


def test_make_database_returns_a_server_wide_postgres_for_postgres_bench() -> None:
    db = make_database(_bench_config("postgres"))
    assert isinstance(db, PostgreSQL)
    # No database means server-wide, the same signal MariaDB uses.
    assert db._database == ""


def test_make_database_raises_for_sqlite_bench() -> None:
    with pytest.raises(DatabaseError, match="SQLite"):
        make_database(_bench_config("sqlite"))


def _write_site_config(bench_root: Path, site: str, cfg: dict) -> None:
    site_dir = bench_root / "sites" / site
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(cfg))


def test_make_site_database_returns_mariadb(tmp_path: Path) -> None:
    _write_site_config(
        tmp_path,
        "mysite.local",
        {
            "db_type": "mariadb",
            "db_name": "mydb",
            "db_user": "myuser",
            "db_password": "mypw",
            "db_socket": "/run/mysqld/mysqld.sock",
        },
    )
    db = make_site_database(tmp_path, "mysite.local")
    assert isinstance(db, MariaDB)


def test_make_site_database_returns_postgres(tmp_path: Path) -> None:
    _write_site_config(
        tmp_path,
        "pgsite.local",
        {
            "db_type": "postgres",
            "db_name": "pgdb",
            "db_user": "pguser",
            "db_password": "pgpw",
        },
    )
    db = make_site_database(tmp_path, "pgsite.local")
    assert isinstance(db, PostgreSQL)


def test_make_site_database_returns_sqlite(tmp_path: Path) -> None:
    _write_site_config(
        tmp_path,
        "litesite.local",
        {
            "db_type": "sqlite",
            "db_name": "litedb",
        },
    )
    db = make_site_database(tmp_path, "litesite.local")
    assert isinstance(db, SQLite)


def test_make_site_database_defaults_to_mariadb(tmp_path: Path) -> None:
    _write_site_config(
        tmp_path,
        "oldsite.local",
        {
            "db_name": "olddb",
            "db_user": "u",
            "db_password": "p",
        },
    )
    db = make_site_database(tmp_path, "oldsite.local")
    assert isinstance(db, MariaDB)


def test_make_site_database_raises_for_missing_site(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ghost"):
        make_site_database(tmp_path, "ghost")


@pytest.mark.parametrize(
    "site_name",
    [
        "../secret-site",
        "../../etc/passwd",
        "foo/../../secret-site",
        "foo/bar",
        "foo\\bar",
        "..",
        "",
    ],
)
def test_make_site_database_rejects_path_traversal(tmp_path: Path, site_name: str) -> None:
    # A sibling directory outside of tmp_path/sites that a traversal attempt
    # could otherwise reach.
    secret_dir = tmp_path.parent / "secret-site"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "site_config.json").write_text(
        json.dumps(
            {
                "db_type": "mariadb",
                "db_name": "d",
                "db_user": "u",
                "db_password": "p",
            }
        )
    )
    try:
        with pytest.raises(FileNotFoundError):
            make_site_database(tmp_path, site_name)
    finally:
        (secret_dir / "site_config.json").unlink()
        secret_dir.rmdir()


def test_bench_db_lazy_init(tmp_path: Path) -> None:
    from pilot.core.bench import Bench

    bench = Bench(_bench_config("mariadb"), tmp_path)
    assert bench._db is None
    db = bench.db
    assert isinstance(db, MariaDB)
    assert bench._db is db  # cached


def test_bench_db_returns_same_instance_on_second_access(tmp_path: Path) -> None:
    from pilot.core.bench import Bench

    bench = Bench(_bench_config("mariadb"), tmp_path)
    assert bench.db is bench.db
