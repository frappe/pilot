"""Entries of the MariaDB slow query log file (log_output=FILE)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pilot.core.database.slow_queries import normalize

TAIL_BYTES = 8 * 1024 * 1024


@dataclass
class SlowLogEntry:
    time: datetime | None = None
    database: str = ""
    thread_id: int = 0
    query_time: float = 0.0
    rows_sent: int = 0
    rows_examined: int = 0
    query: str = ""

    def to_row(self) -> dict:
        """The shape of a mysql.slow_log row, which SlowQueryLog stores."""
        return {
            "db": self.database,
            "sql_text": self.query,
            "query_time": self.query_time,
            "start_time": self.time,
            "thread_id": self.thread_id,
        }


@dataclass
class SlowQuery:
    database: str
    query: str
    calls: int = 0
    duration_seconds: float = 0.0
    rows_examined: int = 0
    rows_sent: int = 0
    example: str = ""


@dataclass
class SlowQueryReport:
    """`log_path` is None when the server keeps no slow log file this host can read."""

    log_path: str | None
    enabled: bool
    long_query_time: float
    queries: list[SlowQuery] = field(default_factory=list)


class SlowLogFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_entries(self, tail_bytes: int = TAIL_BYTES) -> list[SlowLogEntry]:
        """The entries at the end of the slow log."""
        with self.path.open("rb") as log:
            size = log.seek(0, 2)
            log.seek(max(0, size - tail_bytes))
            text = log.read().decode("utf-8", errors="replace")
        return parse_slow_log(text)

    def get_top_queries(self, database: str = "", limit: int = 100) -> list[SlowQuery]:
        """Entries grouped by normalized query, longest total duration first."""
        groups: dict[tuple[str, str], SlowQuery] = {}
        for entry in self.get_entries():
            if database and entry.database != database:
                continue
            key = (entry.database, normalize(entry.query))
            group = groups.setdefault(key, SlowQuery(database=key[0], query=key[1], example=entry.query))
            group.calls += 1
            group.duration_seconds += entry.query_time
            group.rows_examined += entry.rows_examined
            group.rows_sent += entry.rows_sent
        ranked = sorted(groups.values(), key=lambda group: group.duration_seconds, reverse=True)
        for group in ranked:
            group.duration_seconds = round(group.duration_seconds, 3)
        return ranked[:limit]


def parse_slow_log(text: str) -> list[SlowLogEntry]:
    """Each entry starts at its '# Time:' or '# User@Host:' header; the query follows 'SET timestamp'."""
    entries: list[SlowLogEntry] = []
    current: SlowLogEntry | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(("# User@Host:", "# Time:")):
            if current is not None and lines:
                current.query = "\n".join(lines).strip()
                entries.append(current)
                current, lines = None, []
            if line.startswith("# User@Host:"):
                current = SlowLogEntry()
        elif current is not None:
            _read_line(current, line, lines)
    if current is not None and lines:
        current.query = "\n".join(lines).strip()
        entries.append(current)
    return [entry for entry in entries if entry.query and entry.time]


def _read_line(entry: SlowLogEntry, line: str, lines: list[str]) -> None:
    """Read one slow log line into the entry."""
    if match := re.match(r"^# Thread_id: (\d+)\s+Schema: (\S*)", line):
        entry.thread_id, entry.database = int(match[1]), match[2]
    elif match := re.match(
        r"^# Query_time: ([\d.]+)\s+Lock_time: [\d.]+\s+Rows_sent: (\d+)\s+Rows_examined: (\d+)", line
    ):
        entry.query_time, entry.rows_sent, entry.rows_examined = float(match[1]), int(match[2]), int(match[3])
    elif match := re.match(r"^SET timestamp=(\d+);$", line):
        entry.time = datetime.fromtimestamp(int(match[1]))
    elif (
        not line.startswith("#")
        and not re.match(r"^use [^;]+;$", line, flags=re.IGNORECASE)
        and not re.search(r"started with:$|^Tcp port: |^Time\s+Id\s+Command", line)
    ):
        lines.append(line)
