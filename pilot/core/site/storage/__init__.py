from __future__ import annotations

import json
import sys
import typing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pilot.core.server import Server
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.internal.host_resources import DiskUsage

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site

REPORT_FILE_NAME = "site-storage.json"
HISTORY_FILE_NAME = "site-storage-history.json"
HISTORY_DAYS = 30


@dataclass
class SiteStorageUsage:
    name: str
    private_bytes: int
    public_bytes: int
    database_bytes: int
    total_bytes: int


@dataclass
class SiteStorageDay:
    date: str
    total_bytes: int


@dataclass
class SiteStorageReport:
    collected_at: str
    sites: list[SiteStorageUsage] = field(default_factory=list)


class SiteStorageCollector:
    """Every site's files and database size, kept in one file so the Admin API
    answers without walking the disk. The pilot-storage timer refreshes it;
    reach it as `bench.site_storage`."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def path(self) -> Path:
        """The storage report file."""
        return self.bench.logs_path / REPORT_FILE_NAME

    @property
    def history_path(self) -> Path:
        """The daily totals file, one entry per day for the last `HISTORY_DAYS` days."""
        return self.bench.logs_path / HISTORY_FILE_NAME

    def get_report(self) -> SiteStorageReport:
        """The stored report, measured once when there is none. Refreshing it
        is the timer's job, or the refresh-storage-usage task's - never a
        reader's, however old the numbers are."""
        return self.read() or self.collect()

    def read(self) -> SiteStorageReport | None:
        """None for anything unusable: this is a cache, so a truncated file
        means "measure again", not "fail the request"."""
        try:
            payload = json.loads(self.path.read_text())
            return SiteStorageReport(
                collected_at=payload["collected_at"],
                sites=[SiteStorageUsage(**site) for site in payload["sites"]],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def collect(self) -> SiteStorageReport:
        """Measure each site's storage and save the report."""
        sites = self.bench.sites()
        database_bytes = self.get_database_bytes(sites)
        report = SiteStorageReport(
            collected_at=datetime.now(UTC).isoformat(),
            sites=[self.get_usage(site, database_bytes[site.config.name]) for site in sites],
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(self.path, json.dumps(asdict(report), indent=1))
        self.save_history(report)
        return report

    def read_history(self) -> dict[str, dict[str, int]]:
        """Each day's total per site. A history is a nicety, so an unreadable file starts it over."""
        try:
            payload = json.loads(self.history_path.read_text())
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_history(self, report: SiteStorageReport) -> None:
        """Keep the latest total of each site for the report's day, dropping days past the window."""
        days = self.read_history()
        days[report.collected_at[:10]] = {site.name: site.total_bytes for site in report.sites}
        kept = dict(sorted(days.items())[-HISTORY_DAYS:])
        atomic_write_private_text(self.history_path, json.dumps(kept))

    def get_history(self, site_name: str) -> list[SiteStorageDay]:
        """The site's total size on each recorded day, oldest first."""
        return [
            SiteStorageDay(date=day, total_bytes=sizes[site_name])
            for day, sizes in sorted(self.read_history().items())
            if site_name in sizes
        ]

    def get_database_bytes(self, sites: list["Site"]) -> dict[str, int]:
        """One server-wide query covers every site. SQLite has no server to
        ask - each site owns a file under its own directory."""
        if self.bench.config.db_type == "sqlite":
            return {site.config.name: DiskUsage(site.path / "db").bytes for site in sites}
        sizes = self.bench.db.get_schema_sizes()
        return {
            site.config.name: sizes.get(site.config_file.read().get("db_name", ""), 0)
            for site in sites
        }

    @staticmethod
    def get_usage(site: "Site", database_bytes: int) -> SiteStorageUsage:
        """The site's file and database usage."""
        private_bytes = DiskUsage(site.path / "private").bytes
        public_bytes = DiskUsage(site.path / "public").bytes
        return SiteStorageUsage(
            name=site.config.name,
            private_bytes=private_bytes,
            public_bytes=public_bytes,
            database_bytes=database_bytes,
            total_bytes=private_bytes + public_bytes + database_bytes,
        )


def collect_all_benches() -> None:
    """One timer pass over every bench on the host. A stopped database on one
    must not cost the others their refresh."""
    from pilot.core.bench import Bench
    from pilot.core.bench.uploads import BackupUpload
    for bench_path, bench_config in Server().benches.iter_configs():
        bench = Bench(bench_config, bench_path)
        try:
            # Otherwise only a new upload removes abandoned ones.
            BackupUpload.remove_stale(bench)
            SiteStorageCollector(bench).collect()
        except Exception as error:
            print(f"{bench_config.name}: could not collect site storage: {error}", file=sys.stderr)
