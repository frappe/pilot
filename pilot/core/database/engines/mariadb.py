from __future__ import annotations

import time
from pathlib import Path

from pilot.core.database.base import (
    BinlogFile,
    BinlogStatus,
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
    file_modified_ms,
    is_local_host,
    rows_as_dicts,
    validated_process_id,
)
from pilot.exceptions import DatabaseError


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
                    raw = list(cursor.fetchmany(MAX_ROWS + 1))
                    truncated = len(raw) > MAX_ROWS
                    rows = [[r[c] for c in columns] for r in raw[:MAX_ROWS]]
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

    def scan_slow_queries(self, since: tuple[str, str, int] | None = None, limit: int = 5000) -> list[dict]:
        """New mysql.slow_log rows across all schemas, oldest first, for aggregation.

        `mysql.slow_log` has no auto-increment id, so `start_time` alone can't
        page past a batch boundary that lands mid-group of same-timestamp rows:
        a strict `>` drops the rest of the group, and `>=` with no secondary
        key can return the same arbitrary rows forever if a group exceeds
        `limit`. `(start_time, sql_text)` alone still ties for the same query
        text logged at the same instant against a different db (or, on the
        same connection, back-to-back); `thread_id` (the connection) breaks
        that too, so `(start_time, sql_text, thread_id)` is as unique a
        composite key as this table can give us. Row-constructor comparison
        keyset-pages past ties in a stable order, so every row is eventually
        reached exactly once."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                if since:
                    cursor.execute(
                        "SELECT db, sql_text, query_time, start_time, thread_id FROM mysql.slow_log "
                        "WHERE (start_time, sql_text, thread_id) > (%s, %s, %s) "
                        "ORDER BY start_time ASC, sql_text ASC, thread_id ASC LIMIT %s",
                        (since[0], since[1], since[2], int(limit)),
                    )
                else:
                    cursor.execute(
                        "SELECT db, sql_text, query_time, start_time, thread_id FROM mysql.slow_log "
                        "ORDER BY start_time ASC, sql_text ASC, thread_id ASC LIMIT %s",
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

    def get_process_list(self, database: str = "") -> list[dict]:
        rows = rows_as_dicts(self.execute("SHOW FULL PROCESSLIST"))
        if not database:
            return rows
        return [row for row in rows if row.get("db") == database]

    def get_database_size(self) -> DatabaseSize:
        result = self.execute(
            "SELECT COALESCE(SUM(data_length), 0), COALESCE(SUM(index_length), 0), "
            "COALESCE(SUM(data_free), 0) FROM information_schema.TABLES "
            f"WHERE {self._size_scope}"
        )
        data, index, claimable = result.rows[0] if result.rows else (0, 0, 0)
        return DatabaseSize(
            data_bytes=int(data),
            index_bytes=int(index),
            claimable_bytes=int(claimable),
            free_bytes=self._free_disk_bytes(),
        )

    def get_table_sizes(self) -> list[TableSize]:
        result = self.execute(
            "SELECT table_name, COALESCE(data_length, 0), COALESCE(index_length, 0), "
            "COALESCE(data_free, 0) FROM information_schema.TABLES "
            f"WHERE {self._size_scope} ORDER BY data_length + index_length DESC"
        )
        return [
            TableSize(
                name=row[0],
                data_bytes=int(row[1]),
                index_bytes=int(row[2]),
                claimable_bytes=int(row[3]),
            )
            for row in result.rows
        ]

    @property
    def _size_scope(self) -> str:
        """DATABASE() keeps the scope in the connection instead of interpolating
        a name; a connection with no database covers every user schema."""
        if self._database:
            return "table_schema = DATABASE()"
        return "table_schema NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys')"

    def _free_disk_bytes(self) -> int | None:
        """Only meaningful when the server shares this host - the data directory
        path means nothing locally for a remote server."""
        if not is_local_host(self._host, self._socket):
            return None
        return disk_free(str(self.get_scalar("SELECT @@datadir")))

    def kill_process(self, process_id: int) -> None:
        """Drop a connection and roll back whatever it was running."""
        self.execute(f"KILL CONNECTION {validated_process_id(process_id)}", read_only=False)

    def get_active_connections(self) -> int:
        return self.get_status_value("Threads_connected")

    def get_lock_waits(self) -> LockWaitStatus:
        return LockWaitStatus(
            current_waits=self.get_status_value("Innodb_row_lock_current_waits"),
            total_waits=self.get_status_value("Innodb_row_lock_waits"),
            timeout_seconds=int(self.get_scalar("SELECT @@innodb_lock_wait_timeout")),
        )

    def get_lock_wait_rows(self, database: str = "") -> list[LockWaitRow]:
        """Each row is the waiting side of a lock wait, joined to its own
        transaction for state/query/row-count context. INNODB_TRX has no
        database column, so the owning connection supplies it."""
        result = self.execute(
            "SELECT w.requesting_trx_id, l.lock_type, l.lock_mode, l.lock_table, l.lock_index, "
            "t.trx_state, t.trx_started, t.trx_query, t.trx_rows_locked, t.trx_rows_modified, p.DB "
            "FROM information_schema.INNODB_LOCK_WAITS w "
            "JOIN information_schema.INNODB_LOCKS l ON l.lock_id = w.requested_lock_id "
            "JOIN information_schema.INNODB_TRX t ON t.trx_id = w.requesting_trx_id "
            "LEFT JOIN information_schema.PROCESSLIST p ON p.ID = t.trx_mysql_thread_id"
        )
        rows = [row for row in result.rows if not database or row[10] == database]
        return [
            LockWaitRow(
                id=str(row[0]),
                type=row[1],
                mode=row[2],
                table=row[3],
                index=row[4],
                state=row[5],
                started=str(row[6]) if row[6] is not None else None,
                query=row[7],
                rows_locked=int(row[8]) if row[8] is not None else None,
                rows_modified=int(row[9]) if row[9] is not None else None,
            )
            for row in rows
        ]

    def get_binlog_status(self) -> BinlogStatus:
        files = self.get_binlog_files()
        if not files:
            return BinlogStatus(enabled=False, file_count=0, size_bytes=0)
        return BinlogStatus(
            enabled=True,
            file_count=len(files),
            size_bytes=sum(file.size_bytes for file in files),
        )

    def get_binlog_files(self) -> list[BinlogFile]:
        """An enabled binlog always has at least one file (the active one),
        so an empty list doubles as "binlog is off"."""
        if not int(self.get_scalar("SELECT @@log_bin")):
            return []
        directory = Path(str(self.get_scalar("SELECT @@log_bin_basename"))).parent
        logs = self.execute("SHOW BINARY LOGS")
        name_column = logs.columns.index("Log_name")
        size_column = logs.columns.index("File_size")
        return [
            BinlogFile(
                name=row[name_column],
                size_bytes=int(row[size_column]),
                modified_ms=file_modified_ms(directory / row[name_column]),
            )
            for row in logs.rows
        ]

    def purge_binlogs(self, up_to: str) -> None:
        """Delete binlogs older than up_to; the named file itself is kept.

        PURGE is server-managed: it refuses the active file and updates the
        binlog index, so it is safe where deleting files on disk is not."""
        names = [file.name for file in self.get_binlog_files()]
        if up_to not in names:
            raise DatabaseError(f"Unknown binlog file: {up_to}")
        self.execute(f"PURGE BINARY LOGS TO '{up_to}'", read_only=False)

    def get_status_value(self, variable: str) -> int:
        result = self.execute(f"SHOW GLOBAL STATUS LIKE '{variable}'")
        if not result.rows:
            raise DatabaseError(f"Unknown status variable: {variable}")
        return int(result.rows[0][1])

    def get_scalar(self, query: str):
        result = self.execute(query)
        if not result.rows:
            raise DatabaseError(f"Query returned no rows: {query}")
        return result.rows[0][0]
