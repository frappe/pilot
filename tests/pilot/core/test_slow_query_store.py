"""SlowQueryLog normalization, occurrence recording, and capping."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pilot.core.database.slow_queries import MAX_RECORDS, SlowQueryLog, normalize


def _row(sql: str, seconds: float, db: str = "site_db", started: str = "2026-01-01T00:00:00", thread_id: int = 1) -> dict:
    return {
        "db": db,
        "sql_text": sql,
        "query_time": seconds,
        "start_time": datetime.fromisoformat(started),
        "thread_id": thread_id,
    }


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
    assert log.watermark() == ("2026-01-01T00:00:02", "SELECT * FROM t WHERE id = 2 /* t-2 */", 1)


def test_append_separates_by_db(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([_row("SELECT 1", 1.0, db="a"), _row("SELECT 1", 1.0, db="b")])
    assert {r["db"] for r in log.records()} == {"a", "b"}


def test_append_is_defensively_idempotent_on_exact_repeats(tmp_path: Path) -> None:
    # The (start_time, sql_text, thread_id) keyset cursor makes true
    # re-delivery of an already-consumed row unlikely, but append() still
    # guards against it by content fingerprint (e.g. a driver that doesn't
    # report thread_id, so two identical queries at the same microsecond
    # both match the cursor value).
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([
        _row("SELECT 1", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 2", 1.0, started="2026-01-01T00:00:00"),
    ])
    log.append([
        _row("SELECT 1", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 2", 1.0, started="2026-01-01T00:00:00"),
        _row("SELECT 3", 1.0, started="2026-01-01T00:00:00"),
    ])

    records = log.records()
    assert sorted(r["query"] for r in records) == ["SELECT ?", "SELECT ?", "SELECT ?"]
    assert len(records) == 3  # exact repeats collapsed, the new row wasn't dropped


def test_identical_content_at_different_times_is_not_deduped(tmp_path: Path) -> None:
    log = SlowQueryLog(tmp_path / "slow.json")
    log.append([_row("SELECT SLEEP(2)", 2.0, started="2026-01-01T00:00:00")])
    log.append([_row("SELECT SLEEP(2)", 2.0, started="2026-01-01T00:00:05")])

    assert len(log.records()) == 2


def _cursor_key(row: dict) -> tuple:
    return (row["start_time"].isoformat(), row["sql_text"], row["thread_id"])


def test_composite_cursor_makes_progress_past_a_tie_group_larger_than_one_batch(tmp_path: Path) -> None:
    """Simulates scan_slow_queries' (start_time, sql_text, thread_id) keyset
    pagination against an oversized group of rows sharing one start_time,
    batch size 2, to prove every row is reached with no stall or
    duplication - the scenario the position-based `since_count` approach
    couldn't handle."""
    all_rows = [_row(f"SELECT {i}", 1.0, started="2026-01-01T00:00:00", thread_id=i) for i in range(5)]
    all_rows.sort(key=_cursor_key)  # mysql.slow_log's ORDER BY sql_text ASC, thread_id ASC

    def fake_scan(since, limit):
        pool = [r for r in all_rows if since is None or _cursor_key(r) > since]
        return pool[:limit]

    log = SlowQueryLog(tmp_path / "slow.json")
    for _ in range(len(all_rows)):  # one poll per batch, worst case one row of progress each
        batch = fake_scan(log.watermark(), limit=2)
        if not batch:
            break
        log.append(batch)

    records = log.records()
    assert len(records) == 5  # every row reached, none stalled on or dropped
    assert len({r["fp"] for r in records}) == 5  # each is a distinct row, none duplicated


def test_composite_cursor_breaks_ties_across_different_dbs(tmp_path: Path) -> None:
    """The scenario Greptile flagged: the same query text at the exact same
    start_time, but against different dbs (different thread_id too, as two
    separate connections). sql_text alone would tie; thread_id breaks it."""
    all_rows = [
        _row("SELECT SLEEP(1)", 1.0, db="site_a", started="2026-01-01T00:00:00", thread_id=1),
        _row("SELECT SLEEP(1)", 1.0, db="site_b", started="2026-01-01T00:00:00", thread_id=2),
    ]
    all_rows.sort(key=_cursor_key)

    def fake_scan(since, limit):
        pool = [r for r in all_rows if since is None or _cursor_key(r) > since]
        return pool[:limit]

    log = SlowQueryLog(tmp_path / "slow.json")
    for _ in range(len(all_rows)):
        batch = fake_scan(log.watermark(), limit=1)
        if not batch:
            break
        log.append(batch)

    assert {r["db"] for r in log.records()} == {"site_a", "site_b"}


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
