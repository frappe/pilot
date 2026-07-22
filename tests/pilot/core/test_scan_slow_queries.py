"""MariaDB.scan_slow_queries: `>=` includes ties at the watermark; the caller
(SlowQueryLog.append) is responsible for de-duplicating them by content."""

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
    assert "ORDER BY start_time ASC" in query


def test_rescan_uses_gte_and_returns_ties_unfiltered() -> None:
    conn, cursor = _mock_connection([
        {"db": "a", "sql_text": "SELECT 1", "query_time": 1.0, "start_time": "2026-01-01T00:00:00"},
        {"db": "a", "sql_text": "SELECT 2", "query_time": 1.0, "start_time": "2026-01-01T00:00:00"},
    ])
    with patch.object(MariaDB, "_connect", return_value=conn):
        rows = _mariadb().scan_slow_queries(since="2026-01-01T00:00:00")

    query, params = cursor.execute.call_args[0]
    assert "start_time >= %s" in query
    assert params == ("2026-01-01T00:00:00", 5000)
    # scan_slow_queries itself never filters; both ties come back as-is.
    assert [r["sql_text"] for r in rows] == ["SELECT 1", "SELECT 2"]
