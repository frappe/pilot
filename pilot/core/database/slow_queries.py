from __future__ import annotations

import hashlib
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


def fingerprint(db: str, raw_text: str, query_time: float) -> str:
    """Identifies a row by content rather than position, since `mysql.slow_log`
    has no stable ordering for rows tied on `start_time` - a rescan can return
    them in a different order, so position-based de-duplication is unsafe."""
    digest = hashlib.sha1(f"{db}\n{raw_text}\n{query_time}".encode()).hexdigest()
    return digest[:16]


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
        existing = self._read()
        # A rescan re-fetches everything from the watermark onward (`>=`), so
        # rows already recorded at that exact timestamp must be skipped by
        # content, not position - see fingerprint()'s docstring.
        seen = {(record["time"], record["fp"]) for record in existing if "fp" in record}
        new_records = []
        for row in rows:
            raw_text = to_text(row.get("sql_text"))
            normalized = normalize(raw_text)
            when = to_iso(row.get("start_time"))
            seconds = round(to_seconds(row.get("query_time")), 3)
            if not normalized or not when:
                continue
            fp = fingerprint(row.get("db") or "", raw_text, seconds)
            if (when, fp) in seen:
                continue
            seen.add((when, fp))
            new_records.append({
                "time": when,
                "db": row.get("db") or "",
                "query": normalized,
                "query_time": seconds,
                "fp": fp,
            })
        if not new_records:
            return
        records = existing + new_records
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
