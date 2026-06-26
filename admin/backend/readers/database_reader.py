from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pilot.config.mariadb_config import MariaDBConfig


@dataclass
class BinaryLogInfo:
    log_name: str
    file_size: int


@dataclass
class BinlogEvent:
    log_name: str
    pos: int
    event_type: str
    server_id: int
    end_log_pos: int
    info: str


@dataclass
class SlowQuery:
    timestamp: datetime
    query_time: float
    lock_time: float
    rows_examined: int
    rows_sent: int
    user_host: str
    sql: str


_SLOW_QUERY_HEADER = re.compile(r"# Time: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_SLOW_QUERY_USER = re.compile(r"# User@Host:\s*(.+)")
_SLOW_QUERY_STATS = re.compile(
    r"# Query_time:\s*([\d.]+)\s+Lock_time:\s*([\d.]+)\s+"
    r"Rows_sent:\s*(\d+)\s+Rows_examined:\s*(\d+)"
)


class DatabaseReader:
    def __init__(self, mariadb_config: MariaDBConfig) -> None:
        self._config = mariadb_config

    def _connect(self):
        import pymysql

        return pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            user="root",
            password=self._config.root_password,
            unix_socket=self._config.socket_path or None,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def list_binary_logs(self) -> list[BinaryLogInfo]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW BINARY LOGS")
                rows = cursor.fetchall()
            return [BinaryLogInfo(log_name=row["Log_name"], file_size=row["File_size"]) for row in rows]
        finally:
            connection.close()

    def read_binary_log_events(
        self,
        log_name: str,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BinlogEvent]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SHOW BINLOG EVENTS IN %s LIMIT %s, %s",
                    (log_name, offset, limit),
                )
                rows = cursor.fetchall()
            return [
                BinlogEvent(
                    log_name=row["Log_name"],
                    pos=row["Pos"],
                    event_type=row["Event_type"],
                    server_id=row["Server_id"],
                    end_log_pos=row["End_log_pos"],
                    info=row["Info"] or "",
                )
                for row in rows
            ]
        finally:
            connection.close()

    def slow_query_log_path(self) -> Path | None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW VARIABLES LIKE 'slow_query_log'")
                enabled_row = cursor.fetchone()
                if not enabled_row or enabled_row["Value"].lower() != "on":
                    return None
                cursor.execute("SHOW VARIABLES LIKE 'slow_query_log_file'")
                path_row = cursor.fetchone()
            if not path_row or not path_row["Value"]:
                return None
            return Path(path_row["Value"])
        finally:
            connection.close()

    def read_processlist(self) -> list[dict]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW FULL PROCESSLIST")
                return cursor.fetchall()
        finally:
            connection.close()

    def read_slow_queries(self, limit: int = 50) -> list[SlowQuery]:
        log_path = self.slow_query_log_path()
        if log_path is None or not log_path.exists():
            return []
        content = log_path.read_text(errors="replace")
        return _parse_slow_query_log(content, limit)


def _parse_slow_query_log(content: str, limit: int) -> list[SlowQuery]:
    queries: list[SlowQuery] = []
    lines = content.splitlines()
    i = 0

    while i < len(lines):
        time_match = _SLOW_QUERY_HEADER.match(lines[i])
        if time_match:
            query = _parse_one_slow_query(lines, i, time_match)
            if query is not None:
                queries.append(query)
            i += 4
        else:
            i += 1

    return queries[-limit:]


def _parse_one_slow_query(lines: list[str], start: int, time_match) -> SlowQuery | None:
    if start + 3 >= len(lines):
        return None

    user_match = _SLOW_QUERY_USER.match(lines[start + 1]) if start + 1 < len(lines) else None
    stats_match = _SLOW_QUERY_STATS.match(lines[start + 2]) if start + 2 < len(lines) else None

    if not stats_match:
        return None

    sql = lines[start + 3].strip() if start + 3 < len(lines) else ""
    timestamp_str = time_match.group(1)

    try:
        timestamp = datetime.fromisoformat(timestamp_str)
    except ValueError:
        timestamp = datetime.now()

    return SlowQuery(
        timestamp=timestamp,
        query_time=float(stats_match.group(1)),
        lock_time=float(stats_match.group(2)),
        rows_sent=int(stats_match.group(3)),
        rows_examined=int(stats_match.group(4)),
        user_host=user_match.group(1).strip() if user_match else "",
        sql=sql,
    )
