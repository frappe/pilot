"""Apply a retention policy to one site's backups, local and offsite."""

import re
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.backup import BackupConfig
from pilot.config.site_backup import clear_retention, read_retention, write_retention
from pilot.core.site.retention import BackupRetentionPolicy
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.internal.atomic_file import exclusive_file_lock

if TYPE_CHECKING:
    from pilot.core.site import Site

MAX_DAILY_FAILURES = 6
_TS_RE = re.compile(r"^(\d{8}_\d{6})")


def parse_backup_timestamp(filename: str) -> str | None:
    """Return a Frappe backup filename's YYYYMMDD_HHMMSS prefix."""
    match = _TS_RE.match(filename)
    return match.group(1) if match else None


class SiteBackups:
    def __init__(self, site: "Site") -> None:
        self.site = site

    @property
    def directory(self) -> Path:
        """Outside private/backups, which Frappe prunes by age and by its backup limit."""
        return self.site.path / "backups"

    @property
    def runs_lock_path(self) -> Path:
        """Held while a run moves in or retention deletes runs."""
        return self.site.path / "backup_runs"

    def take(self, with_files: bool = True) -> tuple[str, list[Path]]:
        """Run `frappe backup` in its own staging directory and return the run it made."""
        import tempfile

        from pilot.exceptions import BenchError
        from pilot.utils import run_command

        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=self.directory) as staging:
            argv = [
                *self.site.bench.frappe_call,
                "frappe",
                "--site",
                self.site.config.name,
                "backup",
                "--backup-path",
                staging,
            ]
            if with_files:
                argv.append("--with-files")
            # cwd matters: frappe's bench helper reads apps.txt from the current dir.
            run_command(argv, cwd=self.site.bench.sites_path, stream_output=True)
            files = self._publish(sorted(Path(staging).iterdir()))
        timestamp = parse_backup_timestamp(files[0].name) if files else None
        if not timestamp:
            raise BenchError(f"The backup of {self.site.config.name} produced no files.")
        return timestamp, files

    @property
    def is_streaming_supported(self) -> bool:
        """Streams need S3, and Frappe's encryption pass rereads files a FIFO cannot replay."""
        from pilot.core.site.config import is_backup_encryption_enabled

        if not self.site.bench.config.s3.is_configured:
            return False
        return not is_backup_encryption_enabled(self.site.bench.path, self.site.config.name)

    def stream(self, with_files: bool = True) -> tuple[str, dict[str, int]]:
        """Back up straight into S3 and return the run's timestamp and file sizes."""
        from pilot.core.site.backup_stream import SiteBackupStream

        offsite = OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)
        return SiteBackupStream(self.site, offsite).run(with_files)

    def _publish(self, staged: list[Path]) -> list[Path]:
        """Move a run in under the backups lock, so retention sees all of it or none of it."""
        from pilot.exceptions import BenchError

        with exclusive_file_lock(self.runs_lock_path):
            if any((self.directory / path.name).exists() for path in staged):
                raise BenchError(
                    f"A backup of {self.site.config.name} from the same second already exists. Try again."
                )
            return [path.rename(self.directory / path.name) for path in staged]

    def fetch_run(self, timestamp: str, destination: Path) -> list[Path]:
        """One run's files on local disk, downloaded into `destination` when only the
        offsite copy is left."""
        from pilot.exceptions import BenchError

        local = sorted(path for path in self.directory.glob(f"{timestamp}-*") if path.is_file())
        if local:
            return local
        offsite = self._offsite()
        files = offsite.get_backup(self.site.config.name, timestamp) if offsite else None
        if offsite is None or not files:
            raise BenchError(f"Backup {timestamp} of {self.site.config.name} was not found.")
        paths = []
        for filename in files.values():
            offsite.download(self.site.config.name, timestamp, filename, destination / filename)
            paths.append(destination / filename)
        return paths

    def prune(self) -> list[str]:
        """Delete runs rejected by this site's retention policy."""
        config = read_retention(self.site.path / "site_config.json")
        if config is None:
            return []

        offsite = self._offsite()
        offsite_runs = offsite.list_backups(self.site.config.name) if offsite else {}
        policy = BackupRetentionPolicy(config)
        with exclusive_file_lock(self.runs_lock_path):
            timestamps = sorted(set(self._local_timestamps()) | set(offsite_runs))
            selected = policy.select_deletions(timestamps)

        # Offsite deletes are network calls, so publishing does not wait on them.
        deleted = [ts for ts in selected if self._delete_offsite_run(offsite, offsite_runs, ts)]
        with exclusive_file_lock(self.runs_lock_path):
            for timestamp in deleted:
                self._delete_local(timestamp)
        return deleted

    def resolve_file(self, filename: str) -> Path:
        """A path inside this site's own backup directory. These names arrive from API
        requests, so anything that would reach outside it raises instead."""
        from pilot.exceptions import BenchError

        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            raise BenchError("Backup filename is invalid.")
        backups_dir = self.directory.resolve()
        target = (backups_dir / filename).resolve()
        if backups_dir not in target.parents:
            raise BenchError("Backup filename is invalid.")
        return target

    def download_file_path(self, timestamp: str, file_id: str) -> Path:
        from pilot.exceptions import BenchError

        if not file_id.startswith(timestamp):
            raise BenchError("Backup filename is invalid.")
        target = self.resolve_file(file_id)
        if not target.is_file():
            raise FileNotFoundError(file_id)
        return target

    def download_links(self, timestamp: str) -> dict:
        offsite = OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)
        files = offsite.get_backup(self.site.config.name, timestamp)
        if not files:
            raise FileNotFoundError(timestamp)
        return {
            kind: offsite.presigned_url(self.site.config.name, timestamp, filename)
            for kind, filename in files.items()
        }

    def schedule(self) -> dict:
        from pilot.managers.cron import CronManager

        schedule = CronManager(self.site.bench.path).get_schedule(self.site.config.name)
        retention = read_retention(self.site.path / "site_config.json")
        return {"schedule": schedule, "retention": asdict(retention) if retention else None}

    def set_schedule(self, schedule: str, retention: BackupConfig) -> dict:
        from pilot.managers.cron import CronManager

        CronManager(self.site.bench.path).set_schedule(
            self.site.config.name,
            schedule,
            self._cron_command(),
        )
        write_retention(self.site.path / "site_config.json", retention)
        return self.schedule()

    def enable_default_schedule(self) -> None:
        """Default backups for a site without a custom schedule. `set_schedule` replaces them.
        The bench job goes in first, so a failed install leaves the site unenrolled for a retry."""
        self.site.bench.backups.install_schedule()
        write_retention(
            self.site.path / "site_config.json",
            BackupConfig(keep_weekly=4, keep_yearly=10, calendar_rotation=True),
        )

    @property
    def is_default_schedule_enabled(self) -> bool:
        retention = read_retention(self.site.path / "site_config.json")
        return retention is not None and retention.calendar_rotation

    def get_recent_backup_history(self, now: datetime) -> list[tuple[datetime, dict]]:
        return self.site.bench.backups.get_recent_history(now).get(self.site.config.name, [])

    def get_failures_since_last_success(self, now: datetime) -> int:
        failures = 0
        for _, entry in self.get_recent_backup_history(now):
            if entry["status"] == "success":
                break
            failures += entry["status"] == "failed"
        return failures

    def get_scheduled_backup(
        self, now: datetime, history: list[tuple[datetime, dict]] | None = None
    ) -> dict[str, bool] | None:
        """Daily logical backup, due 23 hours after the last success so it keeps its hour. Files once a day."""
        if not self.is_default_schedule_enabled:
            return None

        backups = self.get_recent_backup_history(now) if history is None else history
        successful = [(when, entry) for when, entry in backups if entry["status"] == "success"]
        failures = sum(entry["status"] == "failed" for _, entry in backups)
        if failures >= MAX_DAILY_FAILURES or any(when > now - timedelta(hours=23) for when, _ in successful):
            return None

        has_file_backup_today = any(
            entry.get("with_files") and when.date() == now.date() for when, entry in successful
        )
        return {"with_files": not has_file_backup_today}

    def queue_scheduled(self, now: datetime, history: list[tuple[datetime, dict]] | None = None) -> None:
        from pilot.exceptions import TaskConflictError
        from pilot.tasks.backup_site import BackupSiteTask

        if self.get_scheduled_backup(now, history) is None:
            return

        slot = f"{self.site.config.name}:{now:%Y%m%d%H}"
        try:
            BackupSiteTask.queue(
                self.site.bench,
                site=self.site.config.name,
                automatic=True,
                idempotency_key=f"scheduled-backup:{sha256(slot.encode()).hexdigest()}",
                resource_key=f"site:{self.site.config.name.lower()}",
            )
        except TaskConflictError:
            print(f"Skipped scheduled backup: {self.site.config.name} has an active site task.")

    def move_schedule(self, old_name: str) -> None:
        """Carry a renamed site's backup schedule over from its old name."""
        from pilot.managers.cron import CronManager

        CronManager(self.site.bench.path).move_schedule(old_name, self.site.config.name, self._cron_command())

    def clear_schedule(self) -> None:
        from pilot.managers.cron import CronManager

        CronManager(self.site.bench.path).remove_schedule(self.site.config.name)
        clear_retention(self.site.path / "site_config.json")

    def retention_from_payload(self, block: dict | None):
        return retention_from_payload(block)

    def _delete_offsite_run(self, offsite, offsite_runs: dict, timestamp: str) -> bool:
        """Delete one run offsite. False when that fails, so its local files stay."""
        if timestamp in offsite_runs:
            try:
                self._delete_offsite(offsite, timestamp, offsite_runs[timestamp])
            except Exception as error:
                print(f"Kept backup {timestamp}: offsite delete failed: {error}")
                return False
        return True

    def _local_timestamps(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return [
            timestamp
            for file in self.directory.iterdir()
            if file.is_file() and (timestamp := parse_backup_timestamp(file.name))
        ]

    def _delete_local(self, timestamp: str) -> None:
        if not self.directory.is_dir():
            return
        for file in self.directory.glob(f"{timestamp}-*"):
            file.unlink(missing_ok=True)

    def _delete_offsite(self, offsite: OffsiteBackup, timestamp: str, files: dict[str, str]) -> None:
        for filename in list(files.values()):
            offsite.delete(self.site.config.name, timestamp, filename)

    def _offsite(self) -> OffsiteBackup | None:
        if not self.site.bench.config.s3.is_configured:
            return None
        return OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)

    def _cron_command(self) -> str:
        from pilot.managers.cron import cron_module_command

        return cron_module_command(
            "pilot.tasks.backup_site",
            [self.site.bench.path, self.site.config.name, "--with-files"],
            self.site.bench.logs_path / f"backup-{self.site.config.name}.log",
        )


def retention_from_payload(block: dict | None):
    from pilot.config import VALID_SCHEMES, BackupConfig

    block = block or {}
    config = BackupConfig()
    scheme = str(block.get("scheme", config.scheme)).strip()
    if scheme not in VALID_SCHEMES:
        return f"Retention scheme must be one of: {', '.join(VALID_SCHEMES)}."
    config.scheme = scheme
    for key in config.counts:
        if key not in block:
            continue
        try:
            value = int(block[key])
        except (TypeError, ValueError):
            return f"{key} must be a whole number."
        if value < 0:
            return f"{key} must be zero or more."
        setattr(config, key, value)
    return config
