from __future__ import annotations

from typing import TYPE_CHECKING

import pilot
from pilot.config.common import CommonConfig
from pilot.managers.cron import CronManager, cron_module_command
from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.server import Server

CRON_JOB_KEY = "pilot-auto-update"
CRON_SCHEDULE = "* * * * *"


class AutoUpdate:
    """Daily Pilot update for the host. Reach it as `Server().auto_update`."""

    def __init__(self, server: "Server") -> None:
        self.server = server

    @property
    def is_enabled(self) -> bool:
        return CommonConfig.read(self.server.benches_dir).updates.auto_pilot_update

    def enable(self) -> None:
        log_file = cli_root() / "system" / "logs" / "auto-update.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # A UTC guard works on cron implementations without per-job timezone support.
        command = r'[ "$(date -u +\%H\%M)" = 0000 ] && ' + cron_module_command(
            "pilot.core.server.auto_update", [], log_file
        )
        with CommonConfig.open(self.server.benches_dir) as config:
            CronManager(cli_root()).set_schedule(CRON_JOB_KEY, CRON_SCHEDULE, command)
            config.updates.auto_pilot_update = True

    def disable(self) -> None:
        with CommonConfig.open(self.server.benches_dir) as config:
            CronManager(cli_root()).remove_schedule(CRON_JOB_KEY)
            config.updates.auto_pilot_update = False

    def set_enabled(self, enabled: bool) -> None:
        with CommonConfig.open(self.server.benches_dir) as config:
            config.updates.auto_pilot_update = enabled

    def run(self) -> str | None:
        """Queue a Pilot update on the first bench when a newer release exists. Returns its task id."""
        from pilot.tasks.update_cli import UpdateCliTask
        from pilot.updater import update_available

        if not self.is_enabled or pilot.is_dev_build or not self.server.bench_paths:
            return None
        is_available, latest = update_available()
        if not is_available:
            return None
        bench = self.server.bench(self.server.bench_paths[0])
        return UpdateCliTask.queue(bench, idempotency_key=f"auto-update:{latest}")


if __name__ == "__main__":
    from datetime import UTC, datetime

    from pilot.core.server import Server

    task_id = Server().auto_update.run()
    print(
        f"{datetime.now(UTC).isoformat()} {f'queued update task {task_id}' if task_id else 'no update queued'}"
    )
