"""Tests for pilot.core.database - SQLite is tested live; MariaDB/PostgreSQL use mocks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig, MariaDBConfig, PostgresConfig, RedisConfig, WorkerConfig, WorkerGroup
from pilot.core.database import (
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


def test_mariadb_get_process_list_maps_rows_to_dicts() -> None:
    db = MariaDB(host="h", port=3306, user="u", password="p", database="")
    responses = {
        "PROCESSLIST": (
            ["Id", "User", "db", "Command", "Time", "State", "Info"],
            [[7, "app", "mydb", "Query", 3, "Sending data", "SELECT 1"]],
        )
    }
    with patch.object(MariaDB, "execute", _canned_execute(responses)):
        processes = db.get_process_list()

    assert processes == [
        {"Id": 7, "User": "app", "db": "mydb", "Command": "Query", "Time": 3, "State": "Sending data", "Info": "SELECT 1"}
    ]


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


def test_postgres_get_binlog_status_raises() -> None:
    db = PostgreSQL(host="h", port=5432, user="u", password="p", database="d")
    with pytest.raises(DatabaseError, match="binary log"):
        db.get_binlog_status()


def test_sqlite_server_diagnostics_raise(tmp_path: Path) -> None:
    db = SQLite(str(tmp_path / "x.db"))
    with pytest.raises(DatabaseError, match="no server"):
        db.get_process_list()
    with pytest.raises(DatabaseError, match="no server"):
        db.kill_process(1)
    with pytest.raises(DatabaseError, match="no server"):
        db.get_active_connections()
    with pytest.raises(DatabaseError, match="no server"):
        db.get_lock_waits()
    with pytest.raises(DatabaseError, match="binary log"):
        db.get_binlog_status()


def test_make_database_returns_mariadb_for_mariadb_bench() -> None:
    db = make_database(_bench_config("mariadb"))
    assert isinstance(db, MariaDB)


def test_make_database_returns_postgres_for_postgres_bench() -> None:
    db = make_database(_bench_config("postgres"))
    assert isinstance(db, PostgreSQL)


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
