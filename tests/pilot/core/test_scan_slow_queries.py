"""MariaDB.scan_slow_queries: (start_time, sql_text, thread_id) is a
deterministic composite cursor, so pagination past a group of rows tied on
timestamp (and even sql_text, across dbs/connections) is stable across scans
instead of depending on undefined tie ordering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pilot.core.database.engines import MariaDB


def _mariadb() -> MariaDB:
    return MariaDB(host="localhost", port=3306, user="root", password="", database="")


def _mock_connection(rows: list[dict]):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_first_scan_has_no_since_clause() -> None:
    conn, cursor = _mock_connection([])
    with patch.object(MariaDB, "_connect", return_value=conn):
        _mariadb().scan_slow_queries()

    query = cursor.execute.call_args[0][0]
    assert "WHERE" not in query
    assert "ORDER BY start_time ASC, sql_text ASC, thread_id ASC" in query


def test_rescan_uses_composite_keyset_cursor() -> None:
    conn, cursor = _mock_connection([
        {"db": "a", "sql_text": "SELECT 2", "query_time": 1.0, "start_time": "2026-01-01T00:00:00", "thread_id": 5},
        {"db": "a", "sql_text": "SELECT 3", "query_time": 1.0, "start_time": "2026-01-01T00:00:01", "thread_id": 6},
    ])
    with patch.object(MariaDB, "_connect", return_value=conn):
        rows = _mariadb().scan_slow_queries(since=("2026-01-01T00:00:00", "SELECT 1", 4))

    query, params = cursor.execute.call_args[0]
    assert "(start_time, sql_text, thread_id) > (%s, %s, %s)" in query
    assert "ORDER BY start_time ASC, sql_text ASC, thread_id ASC" in query
    assert params == ("2026-01-01T00:00:00", "SELECT 1", 4, 5000)
    assert [r["sql_text"] for r in rows] == ["SELECT 2", "SELECT 3"]
