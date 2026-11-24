from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from pilot.core.server import Server
from pilot.exceptions import CommandError, DatabaseError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.command import run_command
from pilot.managers.environment import AdminEnvManager
from pilot.managers.systemd_user import systemctl, systemctl_env
from pilot.updater import get_installed_version, perform_upgrade


class DatabaseServerUpdate:
    """A Pilot update of this database VM, which the app VM starts through the agent.

    It runs in a transient user unit, so it outlives the agent it restarts, and it takes no input: the VM updates
    from its own source, as `perform_upgrade` decides.
    """

    unit_name = "pilot-database-update.service"

    @property
    def state_path(self) -> Path:
        """The running or last update."""
        return Server().root / "system" / "database-agent" / "pilot-update.json"

    @property
    def log_path(self) -> Path:
        """The update's output."""
        return Server().root / "system" / "logs" / "database-update.log"

    def get_state(self) -> dict[str, Any]:
        """The running or last update. A run whose unit is gone, for example after a reboot, is reported failed."""
        if not self.state_path.exists():
            return {"status": "idle", "error": "", "version_before": "", "version_after": ""}
        state = json.loads(self.state_path.read_text())
        if state["status"] == "running" and not self.is_unit_active:
            state.update(
                status="failed", error=f"The update stopped before it finished. See {self.log_path}."
            )
        return state

    @property
    def is_unit_active(self) -> bool:
        """Whether the update's unit runs."""
        command = systemctl("is-active", "--quiet", self.unit_name)
        return subprocess.run(command, env=systemctl_env(), check=False).returncode == 0

    def start(self) -> dict[str, Any]:
        """Start an update outside the agent and return its state. While one runs, return that one instead."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.state_path):
            if (state := self.get_state())["status"] == "running":
                return state
            state = {
                "status": "running",
                "error": "",
                "version_before": get_installed_version(),
                "version_after": "",
                "started_at": time.time(),
                "finished_at": None,
            }
            replace_private_text_locked(self.state_path, json.dumps(state))
            try:
                run_command(self.get_launch_command(), env=systemctl_env())
            except CommandError as error:
                replace_private_text_locked(
                    self.state_path, json.dumps(self.get_ended(state, "failed", str(error)))
                )
                raise DatabaseError(f"The database VM could not start the Pilot update: {error}") from error
        return state

    def get_launch_command(self) -> list[str]:
        """Run this module in a transient user unit, which the agent's restart does not stop."""
        root = Server().root
        return [
            "systemd-run",
            "--user",
            f"--unit={self.unit_name}",
            "--collect",
            "--quiet",
            f"--working-directory={root}",
            f"--setenv=PYTHONPATH={root}",
            "--setenv=PYTHONUNBUFFERED=1",
            f"--property=StandardOutput=append:{self.log_path}",
            f"--property=StandardError=append:{self.log_path}",
            str(AdminEnvManager(root).python),
            "-P",
            "-m",
            "pilot.core.database.agent.update",
        ]

    def run(self) -> None:
        """Update Pilot here and record the result. `perform_upgrade` restarts the agent with the new code."""
        try:
            perform_upgrade(on_progress=print)
        except Exception as error:
            # The app VM reads only this file, so every failure must reach it with its reason.
            self.end("failed", f"{error} See {self.log_path} on the database VM.")
            raise
        self.end("succeeded")

    def end(self, status: str, error: str = "") -> None:
        """Record the end of the running update."""
        with exclusive_file_lock(self.state_path):
            state = json.loads(self.state_path.read_text())
            replace_private_text_locked(self.state_path, json.dumps(self.get_ended(state, status, error)))

    @staticmethod
    def get_ended(state: dict[str, Any], status: str, error: str) -> dict[str, Any]:
        """The state of an update that ended, with the version it left on disk."""
        return {
            **state,
            "status": status,
            "error": error,
            "version_after": get_installed_version(),
            "finished_at": time.time(),
        }


# An older Pilot's agent starts this module, so keep this command line stable.
if __name__ == "__main__":
    DatabaseServerUpdate().run()
