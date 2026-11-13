from __future__ import annotations

import json
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.server.legacy_bench.journal import MoveJournal
    from pilot.core.server.legacy_bench.reader import LegacySite
    from pilot.core.site import Site

FILE_PARTS = ("public", "private")
ADOPTED_DATABASE_KEYS = ("db_name", "db_user", "db_password", "db_type")


@dataclass
class SitePlan:
    """One legacy site: its new name, and whether it keeps its database or gets a copy."""

    legacy_site: "LegacySite"
    name: str
    keeps_database: bool


class LegacySiteImport:
    """Brings one site of the moved bench under Pilot. Its old folder waits in `.legacy/sites/`;
    its files move into the new site, and the journal records each move."""

    def __init__(self, plan: SitePlan, bench: "Bench", journal: "MoveJournal") -> None:
        self.plan = plan
        self.bench = bench
        self.journal = journal
        self.stash = bench.path / ".legacy" / "sites" / plan.legacy_site.name

    def run(self, on_progress: Callable[[str], None]) -> "Site":
        site = self.adopt(on_progress) if self.plan.keeps_database else self.restore_copy(on_progress)
        for part in FILE_PARTS:
            if (self.stash / part / "files").is_dir():
                on_progress(f"Moving {part} files...")
                self.journal.move(self.stash / part / "files", site.path / part / "files")
        site.backups.enable_default_schedule()
        return site

    def adopt(self, on_progress: Callable[[str], None]) -> "Site":
        """Register the site on its own database. What belongs to the old host, such as
        `domains`, Redis and Pilot's own keys, is left out."""
        from pilot.core.site import Site
        from pilot.core.site.restore import is_local_config_key

        legacy_site = self.plan.legacy_site
        config = legacy_site.config
        values = {key: value for key, value in config.items() if not is_local_config_key(key)}
        values |= {key: config[key] for key in ADOPTED_DATABASE_KEYS if key in config}
        server = legacy_site.database_server
        values |= (
            {"db_socket": server.socket}
            if server.socket
            else {"db_host": server.host, "db_port": server.port}
        )
        site = Site.adopt(self.bench, self.plan.name, values, on_progress, schedule_backups=False)
        on_progress("Migrating the site...")
        site.migrate()
        return site

    def restore_copy(self, on_progress: Callable[[str], None]) -> "Site":
        """A new site on Pilot's server, restored from a dump of the old database with its config."""
        from pilot.core.server.legacy_bench.database import dump_database
        from pilot.core.site import Site
        from pilot.core.site.restore import BackupRun

        legacy_site = self.plan.legacy_site
        config = legacy_site.config
        with tempfile.TemporaryDirectory(prefix=".import-", dir=self.bench.path) as directory:
            on_progress(f"Dumping the database of {legacy_site.name}...")
            database = Path(directory) / f"{self.plan.name}-database.sql.gz"
            dump_database(
                legacy_site.database_server,
                legacy_site.database_user,
                config["db_password"],
                config["db_name"],
                database,
            )
            config_file = Path(directory) / f"{self.plan.name}-site_config_backup.json"
            config_file.write_text(json.dumps(config))
            config_file.chmod(0o600)
            site = Site.provision(
                self.bench,
                self.plan.name,
                [],
                secrets.token_urlsafe(16),
                on_progress=on_progress,
                schedule_backups=False,
            )
            site.restore_backup(
                BackupRun.from_paths([database, config_file]), ["database", "config"], on_progress
            )
        return site
