from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RestartDatabaseTask(Task):
    command: ClassVar[str] = "restart-database"
    is_cancellable_while_running: ClassVar[bool] = False

    @step("restart", "Restarting MariaDB and checking its health")
    def run(self) -> None:
        """Restart the database server."""
        DatabaseQuickActions(self.bench.config).restart()


if __name__ == "__main__":
    RestartDatabaseTask.main()
