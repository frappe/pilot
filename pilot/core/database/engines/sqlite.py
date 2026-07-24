from __future__ import annotations

import time

from pilot.core.database.base import Database, QueryResult
from pilot.core.database.engines.helpers import MAX_ROWS
from pilot.exceptions import DatabaseError


class SQLite(Database):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        import sqlite3

        conn = self._connect()
        start = time.monotonic()
        try:
            if read_only:
                conn.execute("PRAGMA query_only = 1")
            cursor = conn.execute(query)
            if cursor.description:
                columns = [d[0] for d in cursor.description]
                raw = cursor.fetchmany(MAX_ROWS + 1)
                truncated = len(raw) > MAX_ROWS
                rows = [list(r) for r in raw[:MAX_ROWS]]
            else:
                columns, rows, truncated = [], [], False
            affected = cursor.rowcount or 0
            if not read_only:
                conn.commit()
            return QueryResult(
                columns=columns,
                rows=rows,
                duration_ms=(time.monotonic() - start) * 1000,
                truncated=truncated,
                affected_rows=affected,
            )
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            conn.close()

    def get_tables(self) -> list[str]:
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            return [r[0] for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_table_columns(self, table: str) -> list[dict]:
        safe = table.replace('"', "")
        conn = self._connect()
        try:
            cursor = conn.execute(f'PRAGMA table_info("{safe}")')
            return [{"name": r["name"], "type": r["type"]} for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_schema(self) -> list[dict]:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT m.name AS tbl, p.name AS col, p.type AS typ "
                "FROM sqlite_master m JOIN pragma_table_info(m.name) p "
                "WHERE m.type = 'table' ORDER BY m.name, p.cid"
            )
            columns_by_table: dict[str, list[dict]] = {}
            for r in cursor.fetchall():
                columns_by_table.setdefault(r["tbl"], []).append({"name": r["col"], "type": r["typ"]})
            return [{"name": t, "columns": cols} for t, cols in columns_by_table.items()]
        finally:
            conn.close()

    # SQLite has no server: no process list, connections, lock waits, or
    # binary log. Falls back to Database's default implementations.
