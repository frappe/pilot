from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.exceptions import DatabaseError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetPerformanceSchemaTask(Task):
    command: ClassVar[str] = "set-performance-schema"
    timeout_seconds: ClassVar[int] = 300
    is_cancellable_while_running: ClassVar[bool] = False
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("state",)

    state: Literal["enabled", "disabled"]

    def run(self) -> None:
        if self.state not in ("enabled", "disabled"):
            raise DatabaseError("Performance Schema state must be 'enabled' or 'disabled'.")
        self.configure()

    @step(
        "configure",
        lambda self: f"{'Enabling' if self.state == 'enabled' else 'Disabling'} Performance Schema",
    )
    def configure(self) -> None:
        DatabaseQuickActions(self.bench.config).set_performance_schema(
            self.state == "enabled",
            restart_executor=self.restart,
        )

    @step("restart", "Restarting MariaDB")
    def restart(self, callback: Callable[[], None]) -> None:
        callback()


if __name__ == "__main__":
    SetPerformanceSchemaTask.main()
