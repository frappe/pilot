from __future__ import annotations

from unittest.mock import MagicMock, patch

from pilot.core.database.base import QueryResult
from pilot.core.site.failed_jobs import SiteFailedJobs, get_error_line, group_failures


def test_group_failures_counts_each_job_and_keeps_the_newest() -> None:
    rows = [
        ["insights.refresh", "2026-11-18 10:00:00", "Traceback\n  File x\nTimeoutError: took too long"],
        ["stock.reorder", "2026-11-18 09:00:00", "LinkValidationError: Warehouse missing"],
        ["insights.refresh", "2026-11-18 08:00:00", "older error"],
    ]

    jobs = group_failures(rows)

    assert [(job.job, job.count) for job in jobs] == [("insights.refresh", 2), ("stock.reorder", 1)]
    assert jobs[0].last_failed_at == "2026-11-18 10:00:00"
    assert jobs[0].error == "TimeoutError: took too long"


def test_error_line_handles_empty_details() -> None:
    assert get_error_line(None) == ""
    assert get_error_line("\n  \n") == ""


def test_get_reads_only_recent_failures_from_the_site_database() -> None:
    site = MagicMock()
    database = MagicMock()
    database.quote_identifier.side_effect = lambda name: f"`{name}`"
    database.execute.return_value = QueryResult(columns=[], rows=[["job.a", "2026-11-18 10:00:00", "Boom"]], duration_ms=1)

    with patch("pilot.core.database.make_site_database", return_value=database):
        jobs = SiteFailedJobs(site).get()

    query = database.execute.call_args.args[0]
    assert "`tabScheduled Job Log`" in query
    assert "LEFT JOIN `tabScheduled Job Type`" in query
    assert "log.status = 'Failed'" in query
    assert "LIMIT 200" in query
    assert [job.job for job in jobs] == ["job.a"]
