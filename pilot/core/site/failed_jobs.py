from __future__ import annotations

import typing
from dataclasses import dataclass
from datetime import datetime, timedelta

if typing.TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass
class FailedJob:
    job: str
    count: int
    last_failed_at: str
    error: str


class SiteFailedJobs:
    """Scheduler jobs that failed recently, read from the site's Scheduled Job Log.
    Frappe keeps these in the site database, not in monitor.json.log."""

    row_limit = 200

    def __init__(self, site: "Site") -> None:
        self.site = site

    def get(self, hours: int = 24) -> list[FailedJob]:
        """The failed jobs of the last `hours`, most recent first."""
        from pilot.core.database import make_site_database

        database = make_site_database(self.site.bench.path, self.site.config.name)
        log = database.quote_identifier("tabScheduled Job Log")
        job_type = database.quote_identifier("tabScheduled Job Type")
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        query = (
            f"SELECT COALESCE(job_type.method, log.scheduled_job_type), log.creation, log.details "
            f"FROM {log} log LEFT JOIN {job_type} job_type ON job_type.name = log.scheduled_job_type "
            f"WHERE log.status = 'Failed' AND log.creation >= '{cutoff}' "
            f"ORDER BY log.creation DESC LIMIT {self.row_limit}"
        )
        return group_failures(database.execute(query).rows)


def group_failures(rows: list[list]) -> list[FailedJob]:
    """One entry per job, keeping the newest failure's time and error, rows newest first."""
    jobs: dict[str, FailedJob] = {}
    for name, created, details in rows:
        if name in jobs:
            jobs[name].count += 1
            continue
        jobs[name] = FailedJob(job=name, count=1, last_failed_at=str(created), error=get_error_line(details))
    return list(jobs.values())


def get_error_line(details: str | None) -> str:
    """The last line of a traceback, which names the exception."""
    lines = [line.strip() for line in (details or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""
