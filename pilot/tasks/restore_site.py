import functools
import shutil
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.core.site.restore import BackupRun, BackupStream
from pilot.exceptions import BenchError, RemoteSiteError
from pilot.tasks import Arg, Task, on_cancel, on_failure, step
from pilot.utils import make_private_directory


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
    frappe_cloud_backup: str = ""
    skip_failing_patches: bool = False
    # Pre-signed links by part. Queued with the task, so a rerun still has them after the revoke.
    frappe_cloud_secret_urls: Annotated[dict[str, str] | None, Arg(cli=False)] = None
    frappe_cloud_token: Annotated[str, Arg(cli=False)] = ""

    def run(self) -> None:
        if self.upload_dir and Path(self.upload_dir).resolve().parent != self.bench.uploads_path.resolve():
            raise BenchError(f"Uploaded backups must be in {self.bench.uploads_path}.")
        make_private_directory(self.bench.restores_path, parents=True)
        prefix = f"{self.running_task_id}-"
        with tempfile.TemporaryDirectory(dir=self.bench.restores_path, prefix=prefix) as workdir:
            self.restore(self.fetch(Path(workdir)))
        if self.upload_dir:
            shutil.rmtree(self.upload_dir, ignore_errors=True)

    @on_failure
    @on_cancel
    def cleanup_site_restore(self) -> dict:
        """Backup files can hold a whole database, so a failed run must not leave them, even
        when the task process dies before it can remove them."""
        return {"site": self.site, "upload_dir": self.upload_dir}

    @step("fetch", lambda self: self.fetch_label)
    def fetch(self, workdir: Path) -> BackupRun:
        if self.remote_site:
            return self.fetch_remote(workdir)
        if self.frappe_cloud_secret_urls:
            return self.fetch_frappe_cloud(workdir)
        if self.upload_dir:
            return BackupRun.from_paths(sorted(Path(self.upload_dir).iterdir()))
        backups = self.bench.site(self.source_site).backups
        if self.backup_timestamp:
            return BackupRun.from_paths(backups.fetch_run(self.backup_timestamp, workdir))
        _, paths = backups.take(with_files=self.needs_files)
        return BackupRun.from_paths(paths)

    def fetch_remote(self, workdir: Path) -> BackupRun:
        """Only the config is downloaded here. The other parts stream in during the restore."""
        from pilot.integrations.frappe_site import RemoteFrappeSite

        remote = RemoteFrappeSite(self.remote_site, self.remote_password)
        remote.login()
        timestamp, latest = remote.get_latest_run()
        if timestamp != self.backup_timestamp:
            raise RemoteSiteError("The remote site has a newer backup now. Get its backups again.")
        streamed = self.get_streamed_parts(latest)
        wanted = ["config"] if {"database", "config"} & set(self.parts) else []
        wanted += ["database"] if "database" in self.parts and "database" not in streamed else []
        run = BackupRun.from_paths([remote.download_backup(latest[part], workdir) for part in wanted if latest.get(part)])
        run.streams = {
            part: BackupStream(Path(latest[part]).name, functools.partial(remote.open_backup, latest[part]))
            for part in streamed
        }
        return run

    def fetch_frappe_cloud(self, workdir: Path) -> BackupRun:
        """The links work without the token, so the access ends before the download starts."""
        from pilot.integrations.frappe_cloud import download_backup, open_download_link

        self.bench.site(self.site).frappe_cloud.disconnect(self.frappe_cloud_token)
        links = self.frappe_cloud_secret_urls or {}
        streamed = self.get_streamed_parts(links)
        wanted = ["config"]
        wanted += ["database"] if "database" in self.parts and "database" not in streamed else []
        run = BackupRun.from_paths([download_backup(links[part], workdir) for part in wanted if links.get(part)])
        run.streams = {
            part: BackupStream(
                Path(urllib.parse.urlsplit(links[part]).path).name,
                functools.partial(open_download_link, links[part]),
            )
            for part in streamed
        }
        return run

    def get_streamed_parts(self, available: dict[str, str]) -> list[str]:
        """The parts the restore reads as they download, so they need no copy on disk.
        Only MariaDB can import a database stream."""
        streamable = ["public", "private", *(["database"] if self.bench.config.db_type == "mariadb" else [])]
        return [part for part in streamable if part in self.parts and available.get(part)]

    @step("restore", lambda self: f"Restore {', '.join(self.parts)} into {self.site}")
    def restore(self, run: BackupRun) -> None:
        self.bench.site(self.site).restore_backup(
            run, self.parts, self.report, skip_failing_patches=self.skip_failing_patches
        )

    @property
    def needs_files(self) -> bool:
        return bool({"public", "private"} & set(self.parts))

    @property
    def fetch_label(self) -> str:
        """Streamed sources download in the restore step, so this step only prepares them."""
        if self.frappe_cloud_backup:
            return f"Prepare the Frappe Cloud backup ({self.frappe_cloud_backup})"
        if self.remote_site:
            return f"Prepare the backup from {self.remote_site}"
        return f"Get the backup from {self.source_label}"

    @property
    def source_label(self) -> str:
        if self.remote_site:
            return self.remote_site
        if self.upload_dir:
            return "uploaded files"
        return f"{self.source_site} {self.backup_timestamp}".strip()


if __name__ == "__main__":
    RestoreSiteTask.main()
