import stat
from datetime import UTC, datetime
from types import SimpleNamespace

from pilot.core.bench.audit_log import AuditLog


def _bench(tmp_path):
    return SimpleNamespace(logs_path=tmp_path / "logs")


def test_append_and_read_newest_first(tmp_path) -> None:
    log = AuditLog(_bench(tmp_path))
    log.append("backup", {"site": "a", "status": "success"})
    log.append("backup", {"site": "b", "status": "failed"})

    entries = log.entries()
    assert [e["site"] for e in entries] == ["b", "a"]
    assert all("logged_at" in e for e in entries)


def test_filter_by_type_status_and_site(tmp_path) -> None:
    log = AuditLog(_bench(tmp_path))
    log.append("backup", {"site": "a", "status": "success"})
    log.append("backup", {"site": "a", "status": "failed"})
    log.append("activity", {"site": "a", "status": "success"})

    assert len(log.entries(entry_type="backup")) == 2
    assert len(log.entries(entry_type="backup", status="failed")) == 1
    assert len(log.entries(site="a")) == 3
    assert log.entries(limit=1)[0]["type"] == "activity"


def test_since_limits_polling_to_recent_history_and_includes_the_boundary(tmp_path) -> None:
    log = AuditLog(_bench(tmp_path))
    for day in (15, 16, 17):
        log.append("backup", {"site": "a", "logged_at": f"2026-10-{day:02d}T00:00:00+00:00"})

    records = log.entries(entry_type="backup", site="a", since=datetime(2026, 10, 16, tzinfo=UTC))

    assert [record["logged_at"] for record in records] == [
        "2026-10-17T00:00:00+00:00", "2026-10-16T00:00:00+00:00",
    ]
    assert len(log.entries()) == 3


def test_filter_by_jti_matches_subject_or_actor(tmp_path) -> None:
    log = AuditLog(_bench(tmp_path))
    log.append("session", {"event": "issued", "jti": "a"})
    log.append("session", {"event": "revoked", "jti": "a", "actor_jti": "b"})
    log.append("session", {"event": "issued", "jti": "c"})

    assert {e["event"] for e in log.entries(jti="a")} == {"issued", "revoked"}
    assert [e["event"] for e in log.entries(jti="b")] == ["revoked"]
    assert log.entries(jti="unknown") == []


def test_entries_survive_site_removal(tmp_path) -> None:
    """The log is bench-wide, so an entry stays readable after its site is gone."""
    log = AuditLog(_bench(tmp_path))
    log.append("backup", {"site": "gone", "status": "success"})
    assert AuditLog(_bench(tmp_path)).entries(site="gone")


def test_appends_to_current_iso_week_file(tmp_path) -> None:
    from datetime import datetime

    bench = _bench(tmp_path)
    AuditLog(bench).append("backup", {"site": "a"})

    year, week, _ = datetime.now(UTC).isocalendar()
    path = bench.logs_path / f"audit_{year}_{week:02d}.jsonl"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reads_newest_week_first_across_files(tmp_path) -> None:
    """Older weeks live in separate files; reads span them, newest week first."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "audit_2026_01.jsonl").write_text('{"type": "backup", "site": "old"}\n')
    (logs / "audit_2026_02.jsonl").write_text('{"type": "backup", "site": "new"}\n')

    assert [e["site"] for e in AuditLog(_bench(tmp_path)).entries()] == ["new", "old"]


def test_reversed_lines_across_chunk_boundaries(tmp_path) -> None:
    """The back-to-front chunk reader must reassemble lines split across chunks."""
    from pilot.internal.jsonl_log import JsonlLog

    path = tmp_path / "audit_2026_01.jsonl"
    lines = [f"line-{i:03d}" for i in range(50)]
    path.write_text("\n".join(lines) + "\n")

    assert list(JsonlLog._reversed_lines(path, chunk_size=8)) == list(reversed(lines))


def test_large_log_reads_newest_first_and_limit(tmp_path) -> None:
    bench = _bench(tmp_path)
    log = AuditLog(bench)
    for i in range(500):
        log.append("backup", {"site": f"s{i}", "seq": i})

    newest = log.entries(limit=3)
    assert [e["seq"] for e in newest] == [499, 498, 497]


def test_missing_and_corrupt_lines_are_tolerated(tmp_path) -> None:
    bench = _bench(tmp_path)
    assert AuditLog(bench).entries() == []

    log = AuditLog(bench)
    log.append("backup", {"site": "a"})
    with next(bench.logs_path.glob("audit_*.jsonl")).open("a") as handle:
        handle.write("not json\n")
    log.append("backup", {"site": "b"})

    assert [e["site"] for e in log.entries()] == ["b", "a"]


def test_audit_action_swallows_append_failure(tmp_path, caplog) -> None:
    """bench.audit_action is best-effort: a write failure is logged, never raised."""
    import logging
    from unittest.mock import patch

    from pilot.config import BenchConfig
    from pilot.core.bench import Bench

    bench = Bench(BenchConfig.from_flat("t", {}), tmp_path)
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.bench.audit_log.AuditLog.append", side_effect=OSError("disk full")):
        bench.audit_action("session", {"event": "issued"})

    assert "Audit log update skipped" in caplog.text
