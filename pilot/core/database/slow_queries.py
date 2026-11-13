from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path


def normalize(sql: str) -> str:
    """Strip comments and literals so queries differing only by values group together."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "?", sql)
    sql = re.sub(r"\b\d+\.?\d*\b", "?", sql)
    return re.sub(r"\s+", " ", sql).strip()


def to_text(value: object) -> str:
    """The value as text."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def to_seconds(value: object) -> float:
    """The value in seconds, or 0.0."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def to_iso(value: object) -> str | None:
    """The value as an ISO time, or None."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and value else None


def slow_row_key(row: dict) -> tuple[str, str, int]:
    """The keyset cursor of a slow log row, comparable with SlowQueryLog.watermark()."""
    return (to_iso(row.get("start_time")) or "", to_text(row.get("sql_text")), row.get("thread_id") or 0)


def fingerprint(db: str, raw_text: str, query_time: float, thread_id: object) -> str:
    """A content identity for a row, used as a defense-in-depth duplicate
    check alongside the (start_time, sql_text, thread_id) keyset cursor - see
    SlowQueryLog.watermark()'s docstring for why the cursor alone needs a
    stable composite key."""
    digest = hashlib.sha1(f"{db}\n{raw_text}\n{query_time}\n{thread_id}".encode()).hexdigest()
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

    def watermark(self) -> tuple[str, str, int] | None:
        """(time, raw sql_text, thread_id) - `mysql.slow_log` has no
        auto-increment id, so `start_time` alone can't page past a group of
        rows tied on the same timestamp in a way that's stable across scans.
        `sql_text` breaks most ties, but the same query can still repeat at
        the same instant against a different db or connection; `thread_id`
        (the connection) is the closest thing to a unique tie-breaker this
        table has. `scan_slow_queries` keyset-pages past ties with this
        composite key."""
        watermark = self._read_all().get("watermark")
        return (watermark["time"], watermark["sql_text"], watermark["thread_id"]) if watermark else None

    def records(self) -> list[dict]:
        """The saved slow query records."""
        return self._read_all().get("records", [])

    def append(self, rows: list[dict]) -> None:
        """Add new rows, skipping ones already saved."""
        data = self._read_all()
        existing = data.get("records", [])
        # Defense-in-depth: two rows can share the exact keyset cursor value
        # (e.g. a driver that doesn't report thread_id), which would
        # otherwise repeat forever.
        seen = {(record["time"], record["fp"]) for record in existing if "fp" in record}
        new_records = []
        watermark = data.get("watermark")
        for row in rows:
            raw_text = to_text(row.get("sql_text"))
            normalized = normalize(raw_text)
            when = to_iso(row.get("start_time"))
            seconds = round(to_seconds(row.get("query_time")), 3)
            thread_id = row.get("thread_id") or 0
            if not normalized or not when:
                continue
            fp = fingerprint(row.get("db") or "", raw_text, seconds, thread_id)
            watermark = {"time": when, "sql_text": raw_text, "thread_id": thread_id}
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
        if not new_records and watermark == data.get("watermark"):
            return
        records = existing + new_records
        records.sort(key=lambda r: r["time"])
        self.path.write_text(json.dumps({"watermark": watermark, "records": records[-MAX_RECORDS:]}))

    def _read_all(self) -> dict:
        """The saved data, or an empty dict."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
