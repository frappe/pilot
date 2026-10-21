from __future__ import annotations

import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.bench.audit_log import AuditLog

if TYPE_CHECKING:
    from pilot.core.bench import Bench

CRON_JOB_KEY = "scheduled-backups"
CRON_SCHEDULE = "0 * * * *"


class BenchBackups:
    """The hourly scheduled backup poll for every site on a bench."""

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def install_schedule(self) -> None:
        from pilot.managers.cron import CronManager, cron_module_command

        command = cron_module_command(
            "pilot.core.bench.backups", [self.bench.path], self.bench.logs_path / "scheduled-backups.log"
        )
        CronManager(self.bench.path).set_schedule(CRON_JOB_KEY, CRON_SCHEDULE, command)

    def get_recent_history(self, now: datetime) -> dict[str, list[tuple[datetime, dict]]]:
        """Backup records from the last 24 hours, newest first, by site. One audit log read."""
        history = defaultdict(list)
        for entry in AuditLog(self.bench).entries(entry_type="backup", since=now - timedelta(days=1)):
            if entry.get("event") == "backup":
                history[entry["site"]].append((datetime.fromisoformat(entry["logged_at"]), entry))
        return history

    def queue_scheduled(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        history = self.get_recent_history(now)
        for site in self.bench.sites():
            site.backups.queue_scheduled(now, history.get(site.config.name, []))


if __name__ == "__main__":
    from pilot.core.bench import Bench

    BenchBackups(Bench(Path(sys.argv[1]))).queue_scheduled()
