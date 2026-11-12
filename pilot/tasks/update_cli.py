from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import ClassVar

from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class UpdateCliTask(Task):
    command: ClassVar[str] = "update-cli"

    def run(self) -> None:
        self.update()
        self.restart_admins()

    @step("update", lambda self: f"Update Pilot at {Server().root}")
    def update(self) -> None:
        from pilot.updater import perform_upgrade

        perform_upgrade(on_progress=print)

    @step("restart-admin", lambda self: "Restart every bench's admin service")
    def restart_admins(self) -> None:
        """Every bench's admin imports Pilot's code, so each would mix old and new modules."""
        from pilot.core.server import Server
        from pilot.updater import restart_admin_with_new_code

        failed = []
        for bench_path in Server().bench_paths:
            try:
                restart_admin_with_new_code(bench_path)
            except subprocess.CalledProcessError:
                failed.append(bench_path.name)
        if failed:
            raise BenchError(f"Could not restart the admin of: {', '.join(failed)}")


if __name__ == "__main__":
    UpdateCliTask.main()
