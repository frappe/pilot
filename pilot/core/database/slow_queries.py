from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_NUMERIC = re.compile(r"\b\d+\.?\d*\b")
_WHITESPACE = re.compile(r"\s+")


def normalize(sql: str) -> str:
    """Strip comments and literals so queries differing only by values group together."""
    sql = _COMMENT.sub(" ", sql)
    sql = _STRING.sub("?", sql)
    sql = _NUMERIC.sub("?", sql)
    return _WHITESPACE.sub(" ", sql).strip()


def to_text(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def to_seconds(value: object) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds()
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and value else None


MAX_RECORDS = 20000


class SlowQueryLog:
    """A single bounded JSON file of individual slow-query occurrences (one
    entry per `mysql.slow_log` row), so time-windowed views can bucket real
    occurrence timestamps instead of a single aggregated last-seen time.
    Self-caps at MAX_RECORDS, oldest dropped first, so it needs no external
    log rotation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def watermark(self) -> str | None:
        records = self._read()
        return records[-1]["time"] if records else None

    def records(self) -> list[dict]:
        return self._read()

    def append(self, rows: list[dict]) -> None:
        new_records = []
        for row in rows:
            normalized = normalize(to_text(row.get("sql_text")))
            when = to_iso(row.get("start_time"))
            if not normalized or not when:
                continue
            new_records.append({
                "time": when,
                "db": row.get("db") or "",
                "query": normalized,
                "query_time": round(to_seconds(row.get("query_time")), 3),
            })
        if not new_records:
            return
        records = self._read() + new_records
        records.sort(key=lambda r: r["time"])
        self.path.write_text(json.dumps(records[-MAX_RECORDS:]))

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []
