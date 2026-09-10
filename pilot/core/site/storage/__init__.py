from __future__ import annotations

import json
import sys
import typing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pilot.core.site.config import read_site_config
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.utils import cli_root, directory_bytes, iter_sibling_benches

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site

REPORT_FILE_NAME = "site-storage.json"


@dataclass
class SiteStorageUsage:
    name: str
    private_bytes: int
    public_bytes: int
    database_bytes: int
    total_bytes: int


@dataclass
class SiteStorageReport:
    collected_at: str
    sites: list[SiteStorageUsage] = field(default_factory=list)


class SiteStorageCollector:
    """Every site's files and database size, kept in one file so the Admin API
    answers without walking the disk. The site-storage timer refreshes it;
    reach it as `bench.site_storage`."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.logs_path / REPORT_FILE_NAME

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
        sites = self.bench.sites()
        database_bytes = self.get_database_bytes(sites)
        report = SiteStorageReport(
            collected_at=datetime.now(UTC).isoformat(),
            sites=[self.get_usage(site, database_bytes[site.config.name]) for site in sites],
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(self.path, json.dumps(asdict(report), indent=1))
        return report

    def get_database_bytes(self, sites: list["Site"]) -> dict[str, int]:
        """One server-wide query covers every site. SQLite has no server to
        ask - each site owns a file under its own directory."""
        if self.bench.config.db_type == "sqlite":
            return {site.config.name: directory_bytes(site.path / "db") for site in sites}
        sizes = self.bench.db.get_schema_sizes()
        return {
            site.config.name: sizes.get(read_site_config(site.path).get("db_name", ""), 0)
            for site in sites
        }

    @staticmethod
    def get_usage(site: "Site", database_bytes: int) -> SiteStorageUsage:
        private_bytes = directory_bytes(site.path / "private")
        public_bytes = directory_bytes(site.path / "public")
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

    sentinel = cli_root() / "benches" / ".site-storage-placeholder"
    for bench_path, bench_config in iter_sibling_benches(sentinel):
        try:
            SiteStorageCollector(Bench(bench_config, bench_path)).collect()
        except Exception as error:
            print(f"{bench_config.name}: could not collect site storage: {error}", file=sys.stderr)
