import functools
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Annotated, ClassVar

from pilot.core.site.restore import BackupRun
from pilot.exceptions import BenchError, RemoteSiteError
from pilot.tasks import Arg, Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class RestoreSiteTask(Task):
    """Restore chosen parts of one backup into `site`. The source is a run of a site on this
    bench, a fresh backup of one, uploaded files, or the latest backup of a remote Frappe site."""

    command: ClassVar[str] = "restore-site"
    # Stopping between the database and the files leaves a half-restored site.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    parts: list[str]
    source_site: str = ""
    backup_timestamp: str = ""
    upload_dir: str = ""
    remote_site: str = ""
    remote_password: Annotated[str, Arg(cli=False)] = ""

    def run(self) -> None:
        if self.upload_dir and Path(self.upload_dir).resolve().parent != self.bench.uploads_path.resolve():
            raise BenchError(f"Uploaded backups must be in {self.bench.uploads_path}.")
        with tempfile.TemporaryDirectory(dir=self.bench.path, prefix=".restore-") as workdir:
            run, open_dump = self.fetch(Path(workdir))
            self.restore(run, open_dump)
        if self.upload_dir:
            shutil.rmtree(self.upload_dir, ignore_errors=True)

    @on_failure
    @on_cancel
    def cleanup_site_restore(self) -> dict | None:
        """Uploaded archives can hold a whole database, so a failed run must not leave them."""
        return {"site": self.site, "upload_dir": self.upload_dir} if self.upload_dir else None

    @step("fetch", lambda self: f"Get the backup from {self.source_label}")
    def fetch(self, workdir: Path) -> tuple[BackupRun, Callable[[], IO[bytes]] | None]:
        if self.remote_site:
            return self.fetch_remote(workdir)
        if self.upload_dir:
            return BackupRun.from_paths(sorted(Path(self.upload_dir).iterdir())), None
        backups = self.bench.site(self.source_site).backups
        if self.backup_timestamp:
            return BackupRun.from_paths(backups.fetch_run(self.backup_timestamp, workdir)), None
        _, paths = backups.take(with_files=self.needs_files)
        return BackupRun.from_paths(paths), None

    def fetch_remote(self, workdir: Path) -> tuple[BackupRun, Callable[[], IO[bytes]] | None]:
        """The database streams straight into MariaDB; the file archives are downloaded."""
        from pilot.integrations.frappe_site import RemoteFrappeSite

        remote = RemoteFrappeSite(self.remote_site, self.remote_password)
        remote.login()
        timestamp, latest = remote.get_latest_run()
        if timestamp != self.backup_timestamp:
            raise RemoteSiteError("The remote site has a newer backup now. Get its backups again.")
        is_streamed = "database" in self.parts and self.bench.config.db_type == "mariadb" and "database" in latest
        wanted: list[str] = [part for part in ("public", "private") if part in self.parts]
        wanted += ["config"] if "database" in self.parts else []
        wanted += ["database"] if "database" in self.parts and not is_streamed else []
        run = BackupRun.from_paths([remote.download_backup(latest[part], workdir) for part in wanted if latest.get(part)])
        # Opened by the restore once the local database is ready, so the stream never idles.
        open_dump = functools.partial(remote.open_backup, latest["database"]) if is_streamed else None
        return run, open_dump

    @step("restore", lambda self: f"Restore {', '.join(self.parts)} into {self.site}")
    def restore(self, run: BackupRun, open_dump: Callable[[], IO[bytes]] | None) -> None:
        self.bench.site(self.site).restore_backup(run, self.parts, self.report, open_dump)

    @property
    def needs_files(self) -> bool:
        return bool({"public", "private"} & set(self.parts))

    @property
    def source_label(self) -> str:
        if self.remote_site:
            return self.remote_site
        if self.upload_dir:
            return "uploaded files"
        return f"{self.source_site} {self.backup_timestamp}".strip()


if __name__ == "__main__":
    RestoreSiteTask.main()
