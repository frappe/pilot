from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.database import QueryResult
from pilot.core.database.deadlocks import DeadlockLog, DeadlockParser
from pilot.core.database.engines.mariadb import MariaDB

DUMP = (Path(__file__).parents[2] / "fixtures" / "mariadb_deadlock.txt").read_text()
NOISE = "2026-11-12 12:00:00 0 [Note] InnoDB: Buffer pool(s) load completed\n"


@pytest.fixture(autouse=True)
def server_timezone(monkeypatch: pytest.MonkeyPatch):
    """The log stamps server-local time; pin the zone so the UTC conversion is checkable."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_dump_yields_both_transactions_and_the_victim() -> None:
    [deadlock] = DeadlockParser().parse(NOISE + DUMP + NOISE)

    assert deadlock.time == "2026-11-12T06:58:11+00:00"
    first, second = deadlock.transactions
    assert (first.transaction_id, first.thread_id, first.victim) == ("8172", 580, True)
    assert (second.transaction_id, second.thread_id, second.victim) == ("8173", 582, False)
    assert first.query == "update pilot_dl_test set v=v+1 where id=2"
    assert (first.database, first.table, first.index) == ("_b55b229d4134569a", "pilot_dl_test", "PRIMARY")
    assert first.lock_mode == "X locks rec but not gap"


def test_a_dump_cut_off_by_the_tail_is_kept_without_a_victim() -> None:
    [deadlock] = DeadlockParser().parse(DUMP.rsplit("\n", 2)[0])

    assert len(deadlock.transactions) == 2
    assert not any(transaction.victim for transaction in deadlock.transactions)


def test_the_log_lists_newest_first_and_filters_by_database(tmp_path: Path) -> None:
    log = tmp_path / "error.log"
    log.write_text(DUMP + DUMP.replace("12:28:11", "13:00:00").replace("_b55b229d4134569a", "other"))

    deadlocks = DeadlockLog(log).get_deadlocks()
    assert [deadlock.time for deadlock in deadlocks] == [
        "2026-11-12T07:30:00+00:00",
        "2026-11-12T06:58:11+00:00",
    ]
    assert [deadlock.time for deadlock in DeadlockLog(log).get_deadlocks("other")] == [
        "2026-11-12T07:30:00+00:00"
    ]


def test_the_log_filters_by_a_utc_range(tmp_path: Path) -> None:
    log = tmp_path / "error.log"
    log.write_text(DUMP + DUMP.replace("12:28:11", "13:00:00"))
    since = datetime(2026, 11, 12, 7, 0, tzinfo=UTC)

    assert [deadlock.time for deadlock in DeadlockLog(log).get_deadlocks(since=since)] == [
        "2026-11-12T07:30:00+00:00"
    ]
    assert [deadlock.time for deadlock in DeadlockLog(log).get_deadlocks(until=since)] == [
        "2026-11-12T06:58:11+00:00"
    ]


def test_the_engine_reads_the_server_error_log(tmp_path: Path) -> None:
    log = tmp_path / "mariadb-error.log"
    log.write_text(DUMP)
    report = _engine_report({"@@log_error": str(log), "@@innodb_print_all_deadlocks": 1})

    assert report.log_path == str(log)
    assert report.print_all_deadlocks
    assert len(report.deadlocks) == 1


def test_errors_sent_to_the_journal_mean_no_log_to_read() -> None:
    report = _engine_report({"@@log_error": "", "@@innodb_print_all_deadlocks": 0})

    assert report.log_path is None
    assert not report.print_all_deadlocks
    assert report.deadlocks == []


def _engine_report(variables: dict):
    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        variable = query.removeprefix("SELECT ")
        return QueryResult(columns=[variable], rows=[[variables[variable]]], duration_ms=0)

    engine = MariaDB(host="localhost", port=3306, user="u", password="p", database="")
    with patch.object(MariaDB, "execute", execute):
        return engine.get_deadlocks()
