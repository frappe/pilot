from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.tasks import Task, step

if TYPE_CHECKING:
    from pilot.core.database.agent.client import DatabaseAgentClient


@dataclass(kw_only=True)
class UpdateCliTask(Task):
    command: ClassVar[str] = "update-cli"

    def run(self) -> None:
        """Upgrade Pilot on the database VM of a remote MariaDB first, then here, and restart the Admins."""
        if agent := Server().database_agent:
            self.update_database_server(agent)
        self.update()
        self.restart_admins()

    @step("update-database-server", lambda self: "Update Pilot on the database VM", icon="lucide-database")
    def update_database_server(self, agent: DatabaseAgentClient) -> None:
        """Stop here when it fails, so this VM keeps the code the database VM's agent speaks."""
        agent.update_pilot(on_progress=print)

    @step("update", lambda self: f"Update Pilot at {Server().root}", icon="lucide-git-branch")
    def update(self) -> None:
        """Upgrade Pilot."""
        from pilot.updater import perform_upgrade

        perform_upgrade(on_progress=print)

    @step("restart-admin", lambda self: "Restart every bench's admin service", icon="lucide-refresh-cw")
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
