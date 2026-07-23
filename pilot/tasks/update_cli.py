from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step
from pilot.utils import cli_root


@dataclass(kw_only=True)
class UpdateCliTask(Task):
    command: ClassVar[str] = "update-cli"

    def run(self) -> None:
        self.update()
        self.restart_admin()

    @step("update", lambda self: f"Update bench-cli at {cli_root()}")
    def update(self) -> None:
        from pilot.updater import perform_upgrade

        perform_upgrade(on_progress=print)

    @step("restart-admin", lambda self: "Restart admin service")
    def restart_admin(self) -> None:
        from pilot.managers.processes.local import ProcessManager

        manager = ProcessManager.detect_running(self.bench)
        if type(manager) is ProcessManager:
            return
        manager.restart_admin()


if __name__ == "__main__":
    UpdateCliTask.main()
