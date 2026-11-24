from __future__ import annotations

import json
import os
import typing
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pilot.core.bench.storage.layout import BenchLayout, SiteLayout
from pilot.core.bench.storage.models import BenchBreakdown, SiteStorage, StorageItem
from pilot.core.bench.storage.sizes import FolderSizes
from pilot.exceptions import CommandError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.internal.host_resources import DiskUsage

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site

REPORT_FILE_NAME = "bench-storage.json"


class BenchStorage:
    """The bench folder's storage breakdown, kept in one report file so a page load never
    walks the disk. The refresh-bench-storage task measures it; reach it as `bench.storage`."""

    max_age = timedelta(minutes=10)

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def path(self) -> Path:
        """The report file."""
        return self.bench.logs_path / REPORT_FILE_NAME

    def read(self) -> BenchBreakdown | None:
        """The stored breakdown. None for anything unusable: this is a cache, so a
        truncated file means "measure again", not "fail the request"."""
        try:
            return BenchBreakdown.from_dict(json.loads(self.path.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def is_stale(self, breakdown: BenchBreakdown) -> bool:
        """Whether the breakdown is older than `max_age`."""
        return datetime.fromisoformat(breakdown.measured_at) < datetime.now(UTC) - self.max_age

    def collect(self) -> BenchBreakdown:
        """Measure the bench folder and save the report."""
        breakdown = self.measure()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(self.path, json.dumps(asdict(breakdown), indent=1))
        return breakdown

    def measure(self) -> BenchBreakdown:
        """The breakdown, from one scan of the bench folder."""
        bench = self.bench
        layout = BenchLayout(bench)
        sizes = FolderSizes(bench.path, layout.get_named_paths(), bench.apps_path)
        apps = [sizes.get_app_storage(app) for app in bench.apps()]
        sites = [sizes.get_site_storage(site) for site in bench.sites()]
        others = [
            StorageItem(name=str(path.relative_to(bench.path)), bytes=sizes.get_bytes(path))
            for path in layout.get_other_paths()
        ]
        others.sort(key=lambda item: item.bytes, reverse=True)
        return BenchBreakdown(
            used_bytes=sizes.get_bytes(bench.path),
            apps=apps,
            apps_bytes=sum(item.bytes for item in apps),
            env_bytes=sizes.get_bytes(bench.env_path),
            sites=sites,
            sites_bytes=sum(item.bytes for item in sites),
            logs_bytes=sizes.get_bytes(bench.logs_path),
            others=others,
            others_bytes=sum(item.bytes for item in others),
            database_directory_bytes=self.get_database_directory_bytes(),
            measured_at=datetime.now(UTC).isoformat(),
        )

    def get_database_directory_bytes(self) -> int | None:
        """The database server's data directory, or None when it cannot be measured from here:
        an engine without one, a remote server, or a directory this user cannot read."""
        if self.bench.config.db_type not in {"mariadb", "postgres"}:
            return None
        try:
            path = self.bench.db.get_data_directory()
        except Exception:
            # A stopped server raises its driver's own error; the folder report must not fail with it.
            return None
        if not path or not os.access(path, os.R_OK | os.X_OK):
            return None
        try:
            return DiskUsage(path).bytes
        except (OSError, CommandError):
            return None

    def get_site(self, site: "Site") -> SiteStorage:
        """One site's row, measured now."""
        return FolderSizes(site.path, set(SiteLayout(site.path).get_named_paths())).get_site_storage(site)
