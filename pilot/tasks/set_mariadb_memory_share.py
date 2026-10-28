from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetMariaDBMemoryShareTask(Task):
    command: ClassVar[str] = "set-mariadb-memory-share"
    timeout_seconds: ClassVar[int] = 300
    is_cancellable_while_running: ClassVar[bool] = False
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("memory_share",)

    memory_share: float

    @step("configure", lambda self: f"Set the database memory split to {round(self.memory_share * 100)}%")
    def run(self) -> None:
        DatabaseQuickActions(self.bench.config).set_memory_share(self.memory_share, self.bench.path.parent)


if __name__ == "__main__":
    SetMariaDBMemoryShareTask.main()
