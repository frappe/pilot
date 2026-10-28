from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.exceptions import DatabaseError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetMaxDatabaseConnectionsTask(Task):
    command: ClassVar[str] = "set-max-database-connections"
    timeout_seconds: ClassVar[int] = 300
    is_cancellable_while_running: ClassVar[bool] = False
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("max_connections",)

    max_connections: int

    @step(
        "configure",
        lambda self: f"Setting Max DB Connections to {self.max_connections}",
    )
    def run(self) -> None:
        if type(self.max_connections) is not int:
            raise DatabaseError("Max DB connections must be a whole number.")
        DatabaseQuickActions(self.bench.config).set_max_connections(self.max_connections)


if __name__ == "__main__":
    SetMaxDatabaseConnectionsTask.main()
