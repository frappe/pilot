from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.site import Site

RESTORE_PARTS = ("database", "public", "private", "config")
# The end of tar's error output says why it failed.
_STDERR_TAIL_BYTES = 4096
# Keys that tie a config to its own database, cache, host or Pilot, so they stay as this site has them.
LOCAL_CONFIG_PREFIXES = ("db_", "redis_", "pilot_", "atlas_")
LOCAL_CONFIG_KEYS = frozenset({"rds_db", "host_name", "installed_apps", "maintenance_mode", "pause_scheduler"})


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
class BackupStream:
    """A backup file read as it downloads. Its name tells the part and the compression."""

    name: str
    open: Callable[[], IO[bytes]]


@dataclass
class BackupRun:
    """The files of one backup run, keyed by part: on local disk, or streamed so a large
    file needs no copy on disk."""

    files: dict[str, Path] = field(default_factory=dict)
    streams: dict[str, BackupStream] = field(default_factory=dict)

    @classmethod
    def from_paths(cls, paths: list[Path]) -> BackupRun:
        return cls({part: path for path in paths if (part := backup_part(path.name))})

    @property
    def encryption_key(self) -> str:
        """The source site's key: its database holds passwords encrypted with it."""
        return self.site_config.get("encryption_key", "")

    @property
    def site_config(self) -> dict:
        """The source site's config without its local keys."""
        config = self.files.get("config")
        if not config:
            return {}
        return {key: value for key, value in json.loads(config.read_text()).items() if not is_local_config_key(key)}


def is_local_config_key(key: str) -> bool:
    return key in LOCAL_CONFIG_KEYS or key.startswith(LOCAL_CONFIG_PREFIXES)


class SiteRestore:
    """Restore chosen parts of a backup into this site, then migrate it."""

    def __init__(self, site: Site) -> None:
        self.site = site

    def restore(
        self,
        run: BackupRun,
        parts: list[str],
        on_progress: Callable[[str], None] = print,
        skip_failing_patches: bool = False,
    ) -> None:
        self.require_parts(run, parts)
        self.site.set_maintenance_mode(True)
        try:
            self.restore_parts(run, parts, on_progress)
            on_progress("Migrating the site...")
            self.site.migrate(skip_failing=skip_failing_patches)
        except Exception:
            on_progress(f"Restore failed. {self.site.config.name} stays in maintenance mode.")
            raise
        # Online, not the earlier state: a failed attempt may have left it in maintenance.
        self.site.set_maintenance_mode(False)

    @staticmethod
    def require_parts(run: BackupRun, parts: list[str]) -> None:
        if not parts or any(part not in RESTORE_PARTS for part in parts):
            raise BenchError(f"Choose what to restore: {', '.join(RESTORE_PARTS)}.")
        available = set(run.files) | set(run.streams)
        if missing := [part for part in parts if part not in available]:
            raise BenchError(f"The backup has no {' or '.join(missing)} file.")

    def restore_parts(self, run: BackupRun, parts: list[str], on_progress: Callable[[str], None]) -> None:
        """Files go first: a stream that fails then stops the restore before the database is dropped."""
        for part in ("public", "private"):
            if part in parts:
                if stream := run.streams.get(part):
                    on_progress(f"Downloading and restoring {part} files...")
                    self.extract_files_stream(stream, part)
                else:
                    on_progress(f"Restoring {part} files...")
                    self.extract_files(run.files[part], part)
        if "database" in parts:
            if stream := run.streams.get("database"):
                on_progress("Downloading and restoring the database...")
                self.import_database_stream(stream.open)
            else:
                on_progress("Restoring the database...")
                self.site.restore(str(run.files["database"]))
            self.site.set_config_values(self.get_restored_database_config(run))
        if "config" in parts:
            on_progress("Restoring the site config...")
            values = run.site_config
            if "database" not in parts:
                # The key belongs with its database, and this site keeps its own database.
                values.pop("encryption_key", None)
            self.site.set_config_values(values)

    def get_restored_database_config(self, run: BackupRun) -> dict:
        """Frappe's restore leaves the installed_apps mirror stale, and the restored
        passwords need the source site's encryption key."""
        from pilot.core.site.config import query_installed_apps_via_db

        values: dict = {"encryption_key": run.encryption_key} if run.encryption_key else {}
        apps = query_installed_apps_via_db(self.site.bench.path, self.site.config.name)
        if apps is not None:
            values["installed_apps"] = apps
        return values

    def extract_files(self, archive: Path, part: str) -> None:
        """Frappe's own extraction of ./<site>/<part>/files/, limited to that directory so
        an archive from elsewhere cannot reach site_config.json."""
        run_command(
            ["tar", "xf", str(archive.resolve()), "--strip", "2", "--wildcards", f"*/{part}/files"],
            cwd=self.site.path,
        )

    def extract_files_stream(self, stream: BackupStream, part: str) -> None:
        """`extract_files` for an archive as it downloads. tar cannot detect compression
        on a pipe, so a .tgz needs -z."""
        compression = ["-z"] if stream.name.endswith(".tgz") else []
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                ["tar", "xf", "-", *compression, "--strip", "2", "--wildcards", f"*/{part}/files"],
                cwd=self.site.path,
                stdin=subprocess.PIPE,
                stderr=stderr,
            )
            assert process.stdin is not None
            try:
                with stream.open() as source:
                    shutil.copyfileobj(source, process.stdin)
            except BrokenPipeError:
                pass  # tar exited; its stderr says why
            finally:
                process.stdin.close()
                return_code = process.wait()
            if return_code != 0:
                stderr.seek(max(0, stderr.seek(0, 2) - _STDERR_TAIL_BYTES))
                error = stderr.read().decode(errors="replace").strip()
                raise BenchError(f"Extracting the {part} files failed: {error}")

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
