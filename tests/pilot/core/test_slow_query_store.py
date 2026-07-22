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


def test_rescan_does_not_duplicate_ties_at_the_watermark(tmp_path: Path) -> None:
    # First poll: two rows tied on start_time.
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([
        _row("SELECT 1", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 2", 1.0, started="2026-01-01T00:00:00"),
    ])

    # Rescan (mysql.slow_log has no stable secondary order, so a `>=` rescan
    # can legitimately return the same tied rows back in reversed order) plus
    # one genuinely new row at the same timestamp.
    log.append([
        _row("SELECT 2", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 1", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 3", 1.0, started="2026-01-01T00:00:00"),
    ])

    records = log.records()
    assert sorted(r["query"] for r in records) == ["SELECT ?", "SELECT ?", "SELECT ?"]
    assert len(records) == 3  # no duplicates from the reordered rescan, and the new row wasn't dropped


def test_identical_content_at_different_times_is_not_deduped(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([_row("SELECT SLEEP(2)", 2.0, started="2026-01-01T00:00:00")])
    log.append([_row("SELECT SLEEP(2)", 2.0, started="2026-01-01T00:00:05")])

    assert len(log.records()) == 2


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
