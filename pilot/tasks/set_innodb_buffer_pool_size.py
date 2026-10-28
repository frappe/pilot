from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.exceptions import DatabaseError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetInnoDBBufferPoolSizeTask(Task):
    command: ClassVar[str] = "set-innodb-buffer-pool-size"
    timeout_seconds: ClassVar[int] = 300
    is_cancellable_while_running: ClassVar[bool] = False
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("size_mb",)

    size_mb: int

    @step(
        "configure",
        lambda self: f"Setting InnoDB Buffer Pool size to {self.size_mb} MB",
    )
    def run(self) -> None:
        if type(self.size_mb) is not int:
            raise DatabaseError("InnoDB Buffer Pool size must be a whole number.")
        DatabaseQuickActions(self.bench.config).set_innodb_buffer_pool_size(self.size_mb)


if __name__ == "__main__":
    SetInnoDBBufferPoolSizeTask.main()
