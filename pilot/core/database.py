from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pilot.exceptions import DatabaseError

if TYPE_CHECKING:
    from pilot.config.bench import BenchConfig

_MAX_ROWS = 5000


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    duration_ms: float
    truncated: bool = False
    affected_rows: int = 0


class Database(ABC):
    @abstractmethod
    def execute(self, query: str, read_only: bool = True) -> QueryResult: ...

    @abstractmethod
    def get_tables(self) -> list[str]: ...

    @abstractmethod
    def get_table_columns(self, table: str) -> list[dict]: ...

    def get_schema(self) -> list[dict]:
        return [{"name": t, "columns": self.get_table_columns(t)} for t in self.get_tables()]


class MariaDB(Database):
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, socket: str | None = None) -> None:
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
                columns=columns, rows=rows,
                duration_ms=(time.monotonic() - start) * 1000,
                truncated=truncated, affected_rows=affected,
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
                return [list(r.values())[0] for r in cursor.fetchall()]
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
                tables = [list(r.values())[0] for r in cursor.fetchall()]
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
        except ImportError:
            raise DatabaseError("psycopg2 is not installed. Run: pip install psycopg2-binary")
        return psycopg2.connect(
            host=self._host, port=self._port,
            user=self._user, password=self._password,
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
                columns=columns, rows=rows,
                duration_ms=(time.monotonic() - start) * 1000,
                truncated=truncated, affected_rows=affected,
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
                    columns_by_table.setdefault(table_name, []).append({"name": column_name, "type": data_type})
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
                columns=columns, rows=rows,
                duration_ms=(time.monotonic() - start) * 1000,
                truncated=truncated, affected_rows=affected,
            )
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        finally:
            conn.close()

    def get_tables(self) -> list[str]:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
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


def make_database(config: "BenchConfig") -> Database:
    """Root-level admin database connection for a bench (not site-specific)."""
    if config.db_type == "sqlite":
        raise DatabaseError("SQLite has no shared server; use make_site_database() for site access")
    if config.db_type == "postgres":
        pg = config.postgres
        return PostgreSQL(
            host=pg.host, port=pg.port,
            user=pg.admin_user, password=pg.root_password or "trust_auth",
            database=pg.admin_user,
        )
    mdb = config.mariadb
    return MariaDB(
        host=mdb.host, port=mdb.port,
        user=mdb.admin_user, password=mdb.root_password,
        database="", socket=mdb.socket_path or None,
    )


def make_site_database(bench_root: Path | str, site_name: str) -> Database:
    """Site-specific database connection from site_config.json."""
    # site_name is attacker-controlled (comes straight from request params). Reject
    # anything that isn't a single path segment so it can't escape the sites
    # directory (e.g. "../../etc" or "foo/../../secrets") and read arbitrary
    # site_config.json files (which hold DB credentials) elsewhere on disk.
    if not site_name or "/" in site_name or "\\" in site_name or site_name in (".", ".."):
        raise FileNotFoundError(f"Site '{site_name}' not found")
    cfg_path = Path(bench_root) / "sites" / site_name / "site_config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Site '{site_name}' not found")
    cfg = json.loads(cfg_path.read_text())
    db_type = cfg.get("db_type", "mariadb")
    if db_type == "postgres":
        return PostgreSQL(
            host=cfg.get("db_host", "localhost"),
            port=int(cfg.get("db_port", 5432)),
            user=cfg["db_user"],
            password=cfg["db_password"],
            database=cfg["db_name"],
        )
    if db_type == "sqlite":
        # Frappe stores the site's SQLite database under a `db/` subdirectory
        # (see frappe/database/sqlite/database.py), not directly in the site folder.
        db_file = Path(bench_root) / "sites" / site_name / "db" / f"{cfg.get('db_name', site_name)}.db"
        return SQLite(db_path=str(db_file))
    return MariaDB(
        host=cfg.get("db_host", "localhost"),
        port=int(cfg.get("db_port", 3306)),
        user=cfg["db_user"],
        password=cfg["db_password"],
        database=cfg["db_name"],
        socket=cfg.get("db_socket") or None,
    )
