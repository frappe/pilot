from __future__ import annotations

import typing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if typing.TYPE_CHECKING:
    from pilot.core.database import Database
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

    job_limit = 50
    connect_timeout = 2  # the Overview card should fail fast when the database is down
    default_time_zone = "Asia/Kolkata"  # Frappe's own fallback when System Settings has none

    def __init__(self, site: "Site") -> None:
        self.site = site

    def get(self, hours: int = 24) -> list[FailedJob]:
        """The failed jobs of the last `hours`, one per job, most recent first. The database
        groups the rows, so a job that fails every minute cannot hide the others."""
        from pilot.core.database import make_site_database

        database = make_site_database(
            self.site.bench.path, self.site.config.name, connect_timeout=self.connect_timeout
        )
        cutoff = self.get_cutoff(database, hours)
        mark = database.placeholder
        log = database.quote_identifier("tabScheduled Job Log")
        job_type = database.quote_identifier("tabScheduled Job Type")
        query = (
            "SELECT COALESCE(job_type.method, failed.scheduled_job_type), failed.failures, failed.last_failed_at, "
            f"(SELECT latest.details FROM {log} latest WHERE latest.scheduled_job_type = failed.scheduled_job_type "
            "AND latest.status = 'Failed' AND latest.creation = failed.last_failed_at LIMIT 1) "
            "FROM (SELECT scheduled_job_type, COUNT(*) AS failures, MAX(creation) AS last_failed_at "
            f"FROM {log} WHERE status = 'Failed' AND creation >= {mark} GROUP BY scheduled_job_type) failed "
            f"LEFT JOIN {job_type} job_type ON job_type.name = failed.scheduled_job_type "
            f"ORDER BY failed.last_failed_at DESC LIMIT {mark}"
        )
        return [
            FailedJob(
                job=str(name), count=int(count), last_failed_at=str(last), error=get_error_line(details)
            )
            for name, count, last, details in database.execute(query, params=[cutoff, self.job_limit]).rows
        ]

    def get_cutoff(self, database: "Database", hours: int) -> str:
        """The cutoff in the site's own time zone, the zone Frappe writes `creation` in."""
        singles = database.quote_identifier("tabSingles")
        rows = database.execute(
            f"SELECT value FROM {singles} WHERE doctype = {database.placeholder} AND field = {database.placeholder}",
            params=["System Settings", "time_zone"],
        ).rows
        name = str(rows[0][0]) if rows and rows[0][0] else self.default_time_zone
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo(self.default_time_zone)
        cutoff = datetime.now(UTC).astimezone(zone) - timedelta(hours=hours)
        return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def get_error_line(details: str | None) -> str:
    """The last line of a traceback, which names the exception."""
    lines = [line.strip() for line in (details or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""
