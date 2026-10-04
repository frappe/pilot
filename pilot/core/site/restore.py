from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.site import Site

RESTORE_PARTS = ("database", "public", "private")


def backup_part(filename: str) -> str | None:
    """Which part of a Frappe backup run a file holds, or None for an unknown file."""
    if filename.endswith((".sql.gz", ".sql")):
        return "database"
    if filename.endswith(("-private-files.tar", "-private-files.tgz")):
        return "private"
    if filename.endswith(("-files.tar", "-files.tgz")):
        return "public"
    if filename.endswith("-site_config_backup.json"):
        return "config"
    return None


@dataclass
class BackupRun:
    """The files of one backup run on local disk, keyed by part."""

    files: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def from_paths(cls, paths: list[Path]) -> BackupRun:
        return cls({part: path for path in paths if (part := backup_part(path.name))})

    @property
    def encryption_key(self) -> str:
        """The source site's key: its database holds passwords encrypted with it."""
        config = self.files.get("config")
        if not config:
            return ""
        return json.loads(config.read_text()).get("encryption_key", "")


class SiteRestore:
    """Restore chosen parts of a backup into this site, then migrate it."""

    def __init__(self, site: Site) -> None:
        self.site = site

    def restore(
        self,
        run: BackupRun,
        parts: list[str],
        on_progress: Callable[[str], None] = print,
        open_dump: Callable[[], IO[bytes]] | None = None,
    ) -> None:
        """`open_dump` opens a gzipped SQL dump that streams in, in place of a database file."""
        self.require_parts(run, parts, has_database_stream=open_dump is not None)
        self.site.set_maintenance_mode(True)
        try:
            self.restore_parts(run, parts, on_progress, open_dump)
            on_progress("Migrating the site...")
            self.site.migrate()
        except Exception:
            on_progress(f"Restore failed. {self.site.config.name} stays in maintenance mode.")
            raise
        # Online, not the earlier state: a failed attempt may have left it in maintenance.
        self.site.set_maintenance_mode(False)

    @staticmethod
    def require_parts(run: BackupRun, parts: list[str], has_database_stream: bool = False) -> None:
        if not parts or any(part not in RESTORE_PARTS for part in parts):
            raise BenchError(f"Choose what to restore: {', '.join(RESTORE_PARTS)}.")
        available = set(run.files) | ({"database"} if has_database_stream else set())
        if missing := [part for part in parts if part not in available]:
            raise BenchError(f"The backup has no {' or '.join(missing)} file.")

    def restore_parts(
        self,
        run: BackupRun,
        parts: list[str],
        on_progress: Callable[[str], None],
        open_dump: Callable[[], IO[bytes]] | None,
    ) -> None:
        if "database" in parts:
            on_progress("Restoring the database...")
            if open_dump is not None:
                self.import_database_stream(open_dump)
            else:
                self.site.restore(str(run.files["database"]))
            self.site.set_config_values(self.get_restored_database_config(run))
        for part in ("public", "private"):
            if part in parts:
                on_progress(f"Restoring {part} files...")
                self.extract_files(run.files[part], part)

    def get_restored_database_config(self, run: BackupRun) -> dict:
        """Frappe's restore leaves the installed_apps mirror stale, and the restored
        passwords need the source site's encryption key."""
        from pilot.core.site.config import query_installed_apps_via_db

        values: dict = {}
        apps = query_installed_apps_via_db(self.site.bench.path, self.site.config.name)
        if apps is not None:
            values["installed_apps"] = apps
        if key := run.encryption_key:
            values["encryption_key"] = key
        return values

    def extract_files(self, archive: Path, part: str) -> None:
        """Frappe's own extraction of ./<site>/<part>/files/, limited to that directory so
        an archive from elsewhere cannot reach site_config.json."""
        run_command(
            ["tar", "xf", str(archive.resolve()), "--strip", "2", "--wildcards", f"*/{part}/files"],
            cwd=self.site.path,
        )

    def import_database_stream(self, open_dump: Callable[[], IO[bytes]]) -> None:
        """Replace the site's MariaDB database with the dump as it streams in, so a large
        database needs no copy on disk."""
        from pilot.core.database import site_database_name
        from pilot.managers.database import MariaDBManager

        if self.site.bench.config.db_type != "mariadb":
            raise BenchError("Streaming a database restore needs MariaDB.")
        database = site_database_name(self.site.bench.path, self.site.config.name)
        manager = MariaDBManager(self.site.bench.config.mariadb)
        manager.recreate_database(database)
        with open_dump() as stream, gzip.GzipFile(fileobj=stream) as dump:
            manager.import_sql(database, dump)
