from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from pilot.core.database.base import QueryResult
from pilot.core.site.failed_jobs import SiteFailedJobs, get_error_line


def _database(time_zone: str | None, rows: list[list]) -> MagicMock:
    database = MagicMock()
    database.quote_identifier.side_effect = lambda name: f"`{name}`"
    database.placeholder = "%s"
    database.execute.side_effect = [
        QueryResult(columns=[], rows=[[time_zone]] if time_zone else [], duration_ms=1),
        QueryResult(columns=[], rows=rows, duration_ms=1),
    ]
    return database


def _get(database: MagicMock):
    with patch("pilot.core.database.make_site_database", return_value=database):
        return SiteFailedJobs(MagicMock()).get()


def test_error_line_handles_empty_details() -> None:
    assert get_error_line(None) == ""
    assert get_error_line("\n  \n") == ""


def test_get_groups_failures_in_the_database() -> None:
    database = _database(
        "UTC",
        [
            [
                "insights.refresh",
                900,
                "2026-11-18 10:00:00",
                "Traceback\n  File x\nTimeoutError: took too long",
            ]
        ],
    )

    jobs = _get(database)

    query = database.execute.call_args.args[0]
    assert "GROUP BY scheduled_job_type" in query
    assert "'" not in query.replace("'Failed'", "")
    assert "COUNT(*)" in query
    assert "LEFT JOIN `tabScheduled Job Type`" in query
    assert [(job.job, job.count, job.error) for job in jobs] == [
        ("insights.refresh", 900, "TimeoutError: took too long")
    ]


def test_cutoff_uses_the_site_time_zone() -> None:
    database = _database("America/New_York", [])

    _get(database)

    expected = (datetime.now(UTC).astimezone(ZoneInfo("America/New_York")) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M"
    )
    query = database.execute.call_args.args[0]
    cutoff, limit = database.execute.call_args.kwargs["params"]
    assert "creation >= %s" in query
    assert cutoff.startswith(expected)
    assert limit == 50


def test_cutoff_falls_back_to_frappe_default_zone() -> None:
    database = _database(None, [])

    _get(database)

    expected = (datetime.now(UTC).astimezone(ZoneInfo("Asia/Kolkata")) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M"
    )
    query = database.execute.call_args.args[0]
    cutoff, limit = database.execute.call_args.kwargs["params"]
    assert "creation >= %s" in query
    assert cutoff.startswith(expected)
    assert limit == 50
