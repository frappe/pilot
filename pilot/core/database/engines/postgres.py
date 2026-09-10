from __future__ import annotations

import time

from pilot.core.database.base import (
    Database,
    DatabaseProcess,
    DatabaseSize,
    LockWaitRow,
    LockWaitStatus,
    QueryResult,
    StorageComponent,
    TableSize,
)
from pilot.core.database.engines.helpers import (
    DEFAULT_CONNECT_TIMEOUT,
    MAX_ROWS,
    is_local_host,
    validated_process_id,
)
from pilot.exceptions import DatabaseError


class PostgreSQL(Database):
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._connect_timeout = connect_timeout

    def _connect(self):
        """Every connection is bound to one database, so a server-wide instance
        (empty `database`) uses the maintenance database named after its user."""
        try:
            import psycopg2
        except ImportError as exc:
            raise DatabaseError("psycopg2 is not installed. Run: pip install psycopg2-binary") from exc
        try:
            return psycopg2.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                dbname=self._database or self._user,
                connect_timeout=self._connect_timeout,
            )
        except psycopg2.Error as exc:
            raise DatabaseError(f"Could not connect to PostgreSQL at {self._host}:{self._port}: {exc}") from exc

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

    def get_process_list(self, database: str = "") -> list[DatabaseProcess]:
        """Only client backends: background workers are not connections and
        cannot be killed."""
        result = self.execute(
            "SELECT pid, usename, client_addr, client_port, datname, state, wait_event, "
            "EXTRACT(EPOCH FROM (now() - state_change)), query FROM pg_stat_activity "
            "WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()"
        )
        rows = [row for row in result.rows if not database or row[4] == database]
        return [
            DatabaseProcess(
                id=int(row[0]),
                user=row[1],
                host=f"{row[2]}:{row[3]}" if row[2] else None,
                database=row[4],
                command=row[5],
                state=row[6],
                duration_seconds=float(row[7]) if row[7] is not None else None,
                query=row[8] or None,
            )
            for row in rows
        ]

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
            "SELECT blocked.pid, blocked.locktype, blocked.mode, blocked.relation, "
            "a.state, a.query_start, a.query, a.datname "
            "FROM pg_locks blocked "
            "JOIN pg_stat_activity a ON a.pid = blocked.pid "
            "WHERE NOT blocked.granted"
        )
        rows = [row for row in result.rows if not database or row[7] == database]
        tables = self._table_names(database, rows) if database else {}
        return [
            LockWaitRow(
                id=str(row[0]),
                type=row[1],
                mode=row[2],
                table=self._relation_name(row[7], row[3], tables),
                index=None,
                state=row[4],
                started=str(row[5]) if row[5] is not None else None,
                query=row[6],
                rows_locked=None,
                rows_modified=None,
            )
            for row in rows
        ]

    def _table_names(self, database: str, rows: list[list]) -> dict[int, str]:
        """Resolve relation OIDs only for a selected database. Server-wide
        lock polling must not open one connection per affected database."""
        wanted = {int(row[3]) for row in rows if row[3]}
        if not wanted:
            return {}
        oids = ", ".join(str(oid) for oid in wanted)
        catalog = self._scoped_to(database).execute(
            "SELECT c.oid, n.nspname, c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.oid IN ({oids})"
        )
        return {int(row[0]): f"{database}.{row[1]}.{row[2]}" for row in catalog.rows}

    @staticmethod
    def _relation_name(database: str | None, oid: int | None, names: dict[int, str]) -> str | None:
        if not database or not oid:
            return None
        relation_oid = int(oid)
        return names.get(relation_oid, f"{database} (relation OID {relation_oid})")

    # `pg_table_size` covers the heap and its TOAST, matching what MariaDB
    # reports as data_length. Reclaimable bloat needs the pgstattuple
    # extension, so claimable space stays None.
    _TABLE_SIZE_SOURCE = (
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'm') AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
    )

    def get_database_size(self) -> DatabaseSize:
        """Return an exact split for one database or one combined server total.

        PostgreSQL catalogs cannot split every database's size from one
        connection. Reconnecting to every database makes this request linear in
        the number of sites, so server scope uses pg_database_size instead.
        """
        data, index = self._table_size_totals() if self._database else (None, None)
        return DatabaseSize(
            data_bytes=data,
            index_bytes=index,
            claimable_bytes=None,
            free_bytes=self.get_free_disk_bytes(),
            total_bytes=None if self._database else self._server_database_bytes(),
        )

    def _server_database_bytes(self) -> int:
        result = self.execute(
            "SELECT COALESCE(SUM(size_bytes), 0), COUNT(*) FILTER (WHERE size_bytes IS NULL) "
            "FROM (SELECT pg_database_size(datname) AS size_bytes FROM pg_database "
            "WHERE NOT datistemplate) databases"
        )
        total, unavailable = result.rows[0] if result.rows else (0, 0)
        if int(unavailable):
            raise DatabaseError(f"Could not measure {int(unavailable)} database(s) on this server")
        return int(total)

    def _scoped_to(self, database: str) -> "PostgreSQL":
        return PostgreSQL(
            self._host, self._port, self._user, self._password, database, self._connect_timeout
        )

    def _table_size_totals(self) -> tuple[int, int]:
        result = self.execute(
            "SELECT COALESCE(SUM(pg_table_size(c.oid)), 0), COALESCE(SUM(pg_indexes_size(c.oid)), 0) "
            + self._TABLE_SIZE_SOURCE
        )
        data, index = result.rows[0] if result.rows else (0, 0)
        return int(data), int(index)

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

    def get_data_directory(self) -> str | None:
        if not is_local_host(self._host):
            return None
        result = self.execute("SELECT setting FROM pg_settings WHERE name = 'data_directory'")
        return str(result.rows[0][0]) if result.rows else None

    def get_schema_sizes(self) -> dict[str, int]:
        result = self.execute(
            "SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT datistemplate"
        )
        return {row[0]: int(row[1]) for row in result.rows if row[0] is not None}

    def get_storage_components(self) -> list[StorageComponent]:
        """PostgreSQL's counterpart to the binary log is the WAL, and it has no
        separate slow-query log - slow statements go to the server log."""
        return [
            StorageComponent("wal", "write-ahead log", self._directory_listing_bytes("waldir")),
            StorageComponent("server_log", "server log", self._server_log_bytes()),
        ]

    def _server_log_bytes(self) -> int:
        """With logging_collector off the server logs to stderr, so there is no
        log directory of its own to measure."""
        if str(self.get_scalar("SHOW logging_collector")).lower() not in ("on", "true"):
            return 0
        return self._directory_listing_bytes("logdir")

    def _directory_listing_bytes(self, directory: str) -> int:
        """pg_ls_waldir/pg_ls_logdir report sizes server-side, so they work for
        a remote host and need no filesystem access from here."""
        return int(self.get_scalar(f"SELECT COALESCE(SUM(size), 0) FROM pg_ls_{directory}()"))
