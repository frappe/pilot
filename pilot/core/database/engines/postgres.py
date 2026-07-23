from __future__ import annotations

import time

from pilot.core.database.base import (
    Database,
    DatabaseSize,
    LockWaitRow,
    LockWaitStatus,
    QueryResult,
    TableSize,
)
from pilot.core.database.engines.helpers import (
    MAX_ROWS,
    disk_free,
    is_local_host,
    rows_as_dicts,
    validated_process_id,
)
from pilot.exceptions import DatabaseError


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

    def get_process_list(self, database: str = "") -> list[dict]:
        rows = rows_as_dicts(
            self.execute(
                'SELECT pid, usename AS "user", datname AS "database", state, '
                "EXTRACT(EPOCH FROM (now() - query_start)) AS duration_seconds, query "
                "FROM pg_stat_activity WHERE pid <> pg_backend_pid()"
            )
        )
        if not database:
            return rows
        return [row for row in rows if row.get("database") == database]

    def kill_process(self, process_id: int) -> None:
        """pg_terminate_backend reports a missing backend by returning false."""
        pid = validated_process_id(process_id)
        result = self.execute(f"SELECT pg_terminate_backend({pid})")
        if not result.rows or not result.rows[0][0]:
            raise DatabaseError(f"No such process: {pid}")

    def get_active_connections(self) -> int:
        return int(self.execute("SELECT COUNT(*) FROM pg_stat_activity").rows[0][0])

    def get_lock_waits(self) -> LockWaitStatus:
        current = int(self.execute("SELECT COUNT(*) FROM pg_locks WHERE NOT granted").rows[0][0])
        timeout_ms = int(self.execute("SELECT setting FROM pg_settings WHERE name = 'lock_timeout'").rows[0][0])
        # PostgreSQL keeps no cumulative lock-wait counter; lock_timeout of 0 means disabled.
        return LockWaitStatus(
            current_waits=current,
            total_waits=None,
            timeout_seconds=timeout_ms // 1000 if timeout_ms else None,
        )

    def get_lock_wait_rows(self, database: str = "") -> list[LockWaitRow]:
        """PostgreSQL has no lock-index concept and no per-transaction row
        counters, so index/rows_locked/rows_modified are always None."""
        result = self.execute(
            "SELECT blocked.pid, blocked.locktype, blocked.mode, "
            "blocked.relation::regclass::text, a.state, a.query_start, a.query, a.datname "
            "FROM pg_locks blocked "
            "JOIN pg_stat_activity a ON a.pid = blocked.pid "
            "WHERE NOT blocked.granted"
        )
        rows = [row for row in result.rows if not database or row[7] == database]
        return [
            LockWaitRow(
                id=str(row[0]),
                type=row[1],
                mode=row[2],
                table=row[3],
                index=None,
                state=row[4],
                started=str(row[5]) if row[5] is not None else None,
                query=row[6],
                rows_locked=None,
                rows_modified=None,
            )
            for row in rows
        ]

    # `pg_table_size` covers the heap and its TOAST, matching what MariaDB
    # reports as data_length. Reclaimable bloat needs the pgstattuple
    # extension, so claimable space stays None.
    _TABLE_SIZE_SOURCE = (
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'm') AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
    )

    def get_database_size(self) -> DatabaseSize:
        result = self.execute(
            "SELECT COALESCE(SUM(pg_table_size(c.oid)), 0), COALESCE(SUM(pg_indexes_size(c.oid)), 0) "
            + self._TABLE_SIZE_SOURCE
        )
        data, index = result.rows[0] if result.rows else (0, 0)
        return DatabaseSize(
            data_bytes=int(data),
            index_bytes=int(index),
            claimable_bytes=None,
            free_bytes=self._free_disk_bytes(),
        )

    def get_table_sizes(self) -> list[TableSize]:
        result = self.execute(
            "SELECT c.relname, pg_table_size(c.oid), pg_indexes_size(c.oid) "
            + self._TABLE_SIZE_SOURCE
            + " ORDER BY pg_total_relation_size(c.oid) DESC"
        )
        return [
            TableSize(
                name=row[0],
                data_bytes=int(row[1]),
                index_bytes=int(row[2]),
                claimable_bytes=None,
            )
            for row in result.rows
        ]

    def _free_disk_bytes(self) -> int | None:
        if not is_local_host(self._host):
            return None
        result = self.execute("SELECT setting FROM pg_settings WHERE name = 'data_directory'")
        if not result.rows:
            return None
        return disk_free(str(result.rows[0][0]))

    # No binary log: WAL archiving is configured server-side. Falls back to
    # Database's default get_binlog_status/get_binlog_files/purge_binlogs.
