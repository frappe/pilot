from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.tasks import Task, step

if TYPE_CHECKING:
    from pilot.core.agent.client import AgentClient


@dataclass(kw_only=True)
class UpdateCliTask(Task):
    command: ClassVar[str] = "update-cli"

    def run(self) -> None:
        """Upgrade Pilot on the VMs this host manages first, then here, and restart the Admins."""
        if agents := Server().agents:
            self.update_connected_servers(agents)
        self.update()
        self.restart_admins()

    @step("update-connected-servers", lambda self: "Update Pilot on connected VMs", icon="lucide-server")
    def update_connected_servers(self, agents: list[AgentClient]) -> None:
        """Stop at the first failure, so this VM keeps the code the remaining agents speak."""
        for agent in agents:
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
