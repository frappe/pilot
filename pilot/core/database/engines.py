from __future__ import annotations

import time

from pilot.core.database.base import Database, QueryResult
from pilot.exceptions import DatabaseError

_MAX_ROWS = 5000


class MariaDB(Database):
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        socket: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._socket = socket

    def _connect(self):
        import pymysql
        import pymysql.cursors

        return pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=self._database or None,
            unix_socket=self._socket,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        import pymysql

        conn = self._connect()
        start = time.monotonic()
        try:
            with conn.cursor() as cursor:
                if read_only:
                    cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    raw = list(cursor.fetchmany(_MAX_ROWS + 1))
                    truncated = len(raw) > _MAX_ROWS
                    rows = [[r[c] for c in columns] for r in raw[:_MAX_ROWS]]
                else:
                    columns, rows, truncated = [], [], False
                affected = cursor.rowcount or 0
            if read_only:
                conn.rollback()
            else:
                conn.commit()
            return QueryResult(
                columns=columns,
                rows=rows,
                duration_ms=(time.monotonic() - start) * 1000,
                truncated=truncated,
                affected_rows=affected,
            )
        except pymysql.Error as exc:
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            conn.close()

    def get_tables(self) -> list[str]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                return [next(iter(r.values())) for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_table_columns(self, table: str) -> list[dict]:
        safe = table.replace("`", "")
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM `{safe}`")
                return [{"name": r["Field"], "type": r["Type"]} for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_schema(self) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = [next(iter(r.values())) for r in cursor.fetchall()]
                cursor.execute(
                    "SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col, COLUMN_TYPE AS typ "
                    "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                )
                columns_by_table: dict[str, list[dict]] = {}
                for r in cursor.fetchall():
                    columns_by_table.setdefault(r["tbl"], []).append({"name": r["col"], "type": r["typ"]})
            return [{"name": t, "columns": columns_by_table.get(t, [])} for t in tables]
        finally:
            conn.close()

    def get_global_status(self) -> dict[str, str]:
        return self._name_value_pairs("SHOW GLOBAL STATUS")

    def get_global_variables(self) -> dict[str, str]:
        return self._name_value_pairs("SHOW GLOBAL VARIABLES")

    def is_slow_log_enabled(self) -> bool:
        variables = self.get_global_variables()
        return variables.get("slow_query_log") == "ON" and "TABLE" in (variables.get("log_output") or "")

    def enable_slow_log(self, long_query_time: float = 1.0) -> None:
        """Mutates shared server globals; only call on an explicit user action."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET GLOBAL slow_query_log = ON")
                cursor.execute("SET GLOBAL log_output = 'TABLE'")
                cursor.execute("SET GLOBAL long_query_time = %s", (long_query_time,))
        finally:
            conn.close()

    def scan_slow_queries(self, since: str | None = None, limit: int = 5000) -> list[dict]:
        """New mysql.slow_log rows across all schemas, oldest first, for aggregation."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                if since:
                    cursor.execute(
                        "SELECT db, sql_text, query_time, start_time FROM mysql.slow_log "
                        "WHERE start_time > %s ORDER BY start_time ASC LIMIT %s",
                        (since, int(limit)),
                    )
                else:
                    cursor.execute(
                        "SELECT db, sql_text, query_time, start_time FROM mysql.slow_log "
                        "ORDER BY start_time ASC LIMIT %s",
                        (int(limit),),
                    )
                return list(cursor.fetchall())
        finally:
            conn.close()

    def _name_value_pairs(self, query: str) -> dict[str, str]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(query)
                return {r["Variable_name"]: r["Value"] for r in cursor.fetchall()}
        finally:
            conn.close()


class PostgreSQL(Database):
    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database

    def _connect(self):
        try:
            import psycopg2
        except ImportError as exc:
            raise DatabaseError("psycopg2 is not installed. Run: pip install psycopg2-binary") from exc
        return psycopg2.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            dbname=self._database,
        )

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        conn = self._connect()
        start = time.monotonic()
        try:
            if read_only:
                conn.set_session(readonly=True)
            with conn.cursor() as cursor:
                cursor.execute(query)
                if cursor.description:
                    columns = [d[0] for d in cursor.description]
                    raw = cursor.fetchmany(_MAX_ROWS + 1)
                    truncated = len(raw) > _MAX_ROWS
                    rows = [list(r) for r in raw[:_MAX_ROWS]]
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
        except Exception as exc:
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            conn.close()

    def get_tables(self) -> list[str]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )
                return [r[0] for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_table_columns(self, table: str) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                    (table,),
                )
                return [{"name": r[0], "type": r[1]} for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_schema(self) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )
                tables = [r[0] for r in cursor.fetchall()]
                cursor.execute(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                )
                columns_by_table: dict[str, list[dict]] = {}
                for table_name, column_name, data_type in cursor.fetchall():
                    columns_by_table.setdefault(table_name, []).append(
                        {"name": column_name, "type": data_type}
                    )
            return [{"name": t, "columns": columns_by_table.get(t, [])} for t in tables]
        finally:
            conn.close()


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
                raw = cursor.fetchmany(_MAX_ROWS + 1)
                truncated = len(raw) > _MAX_ROWS
                rows = [list(r) for r in raw[:_MAX_ROWS]]
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
