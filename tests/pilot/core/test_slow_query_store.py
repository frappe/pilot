"""SlowQueryLog normalization, occurrence recording, and capping."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pilot.core.database.slow_queries import MAX_RECORDS, SlowQueryLog, normalize


def _row(sql: str, seconds: float, db: str = "site_db", started: str = "2026-01-01T00:00:00") -> dict:
    return {"db": db, "sql_text": sql, "query_time": seconds, "start_time": datetime.fromisoformat(started)}


def test_normalize_strips_comments_and_literals() -> None:
    assert normalize("/* trace */ SELECT * FROM t WHERE a = 'x' AND b = 42") == "SELECT * FROM t WHERE a = ? AND b = ?"


def test_append_records_each_occurrence(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([
        _row("SELECT * FROM t WHERE id = 1 /* t-1 */", 1.0, started="2026-01-01T00:00:01"),
        _row("SELECT * FROM t WHERE id = 2 /* t-2 */", 3.0, started="2026-01-01T00:00:02"),
    ])

    records = log.records()
    assert len(records) == 2
    assert records[0]["query"] == "SELECT * FROM t WHERE id = ?"
    assert records[1]["query_time"] == 3.0
    assert log.watermark() == "2026-01-01T00:00:02"


def test_append_separates_by_db(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([_row("SELECT 1", 1.0, db="a"), _row("SELECT 1", 1.0, db="b")])
    assert {r["db"] for r in log.records()} == {"a", "b"}


def test_records_sorted_and_capped_to_max_records(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    rows = [
        _row(f"SELECT * FROM t{i}", 1.0, started=f"2026-01-01T00:{i % 60:02d}:00")
        for i in range(MAX_RECORDS + 10)
    ]
    log.append(rows)
    records = log.records()
    assert len(records) == MAX_RECORDS
    assert records == sorted(records, key=lambda r: r["time"])
