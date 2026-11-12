from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.core.site.hourly_rollup import RequestRollup, UptimeRollup

NOW = datetime(2026, 11, 12, 15, 30, tzinfo=UTC)
SITE = "one.example.com"


def _bench(tmp_path: Path) -> SimpleNamespace:
    (tmp_path / "logs").mkdir(exist_ok=True)
    return SimpleNamespace(logs_path=tmp_path / "logs")


def _append(path: Path, *records: dict, partial: str = "") -> None:
    with path.open("a") as log:
        log.writelines(json.dumps(record) + "\n" for record in records)
        log.write(partial)


def _check(time: str, up: bool = True, **extra) -> dict:
    return {"time": time, "site": SITE, "up": up, **extra}


def test_uptime_counts_checks_per_hour_and_skips_maintenance(tmp_path: Path) -> None:
    rollup = UptimeRollup(_bench(tmp_path))
    _append(
        rollup.source,
        _check("2026-11-12T14:10:00+00:00"),
        _check("2026-11-12T14:20:00+00:00", up=False),
        _check("2026-11-12T15:05:00+00:00"),
        _check("2026-11-12T15:06:00+00:00", up=False, maintenance=True),
    )
    rollup.update(NOW)

    assert rollup.read_state()["sites"][SITE] == {"2026-11-12T14": [1, 2], "2026-11-12T15": [1, 1]}
    assert rollup.get_percent(SITE, NOW) == pytest.approx(66.67)
    assert rollup.get_percent("unknown.example.com", NOW) is None


def test_an_update_reads_only_new_whole_lines(tmp_path: Path) -> None:
    rollup = UptimeRollup(_bench(tmp_path))
    half = json.dumps(_check("2026-11-12T15:10:00+00:00"))
    _append(rollup.source, _check("2026-11-12T15:00:00+00:00"), partial=half[:20])
    rollup.update(NOW)
    assert rollup.read_state()["sites"][SITE]["2026-11-12T15"] == [1, 1]

    with rollup.source.open("a") as log:
        log.write(half[20:] + "\n")
    rollup.update(NOW)
    rollup.update(NOW)
    assert rollup.read_state()["sites"][SITE]["2026-11-12T15"] == [2, 2]


def test_a_truncated_log_is_read_from_the_start(tmp_path: Path) -> None:
    rollup = UptimeRollup(_bench(tmp_path))
    _append(rollup.source, *[_check("2026-11-12T15:00:00+00:00")] * 3)
    rollup.update(NOW)

    rollup.source.write_text(json.dumps(_check("2026-11-12T15:20:00+00:00", up=False)) + "\n")
    rollup.update(NOW)

    assert rollup.read_state()["sites"][SITE]["2026-11-12T15"] == [3, 4]


def test_hours_older_than_the_retention_are_dropped(tmp_path: Path) -> None:
    rollup = RequestRollup(_bench(tmp_path))
    request = {"site": SITE, "transaction_type": "request", "request": {"path": "/app"}}
    _append(
        rollup.source,
        {**request, "timestamp": "2026-11-11 10:00:00+00:00"},
        {**request, "timestamp": "2026-11-12 15:01:00+00:00"},
        {**request, "timestamp": "2026-11-12 15:02:00+00:00", "request": {"path": "/api/method/ping"}},
        {**request, "timestamp": "2026-11-12 15:03:00+00:00", "transaction_type": "job"},
    )
    rollup.update(NOW)

    assert rollup.read_state()["sites"][SITE] == {"2026-11-12T15": 1}
    counts = rollup.get_counts(SITE, NOW)
    assert len(counts) == 24
    assert counts[-1] == 1
    assert sum(counts) == 1
