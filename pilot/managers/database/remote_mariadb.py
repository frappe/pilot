from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

from pilot.core.database.agent.client import DatabaseAgentClient
from pilot.core.mariadb_memory import MariaDBMemorySizing
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager


class RemoteMariaDBManager(MariaDBManager):
    """A MariaDB on its own VM, which Pilot manages through the database agent there.

    SQL goes over TCP. The agent owns the server's option files, service, logs and disk, and the database VM
    sizes the server for its own memory, so this host writes nothing of the server's.
    """

    # A restart may block while the server recovers from a crash.
    service_timeout = 15 * 60
    # Pilot's option file uses options that older servers refuse to start with.
    minimum_version = (11, 8)

    @cached_property
    def agent(self) -> DatabaseAgentClient:
        """The database agent on the server's VM."""
        return DatabaseAgentClient.for_config(self.config)

    def get_health(self) -> dict[str, Any]:
        """The server's state, as the agent reports it."""
        return self.agent.get_health()

    @property
    def state_dir(self) -> Path:
        """Local state only: the action lock that serializes this host's changes to the remote server."""
        from pilot.core.server import Server

        return Server().root / "databases" / "mariadb-remote"

    def is_installed(self) -> bool:
        """Whether the database VM has the MariaDB server."""
        return self.get_health()["is_installed"]

    def is_provisioned(self) -> bool:
        """Whether the database VM has set up its server."""
        return self.get_health()["is_provisioned"]

    def is_running(self) -> bool:
        """Whether the remote server runs."""
        return self.get_health()["is_running"]

    def provision(self) -> None:
        """Take over the remote server: create the admin account for this host's address."""
        if not self.config.root_password:
            raise DatabaseError("Set mariadb.root_password before Pilot takes over the remote server.")
        health = self.get_health()
        if not health["is_provisioned"]:
            raise DatabaseError(f"Run `pilot setup database-server` on {self.config.host} first.")
        if tuple(health["version"]) < self.minimum_version:
            raise DatabaseError(
                f"Pilot needs MariaDB {'.'.join(map(str, self.minimum_version))} or newer on "
                f"{self.config.host}, which runs {'.'.join(map(str, health['version']))}."
            )

        with self.database_action_lock():
            self.secure_installation()
            self._wait_until_reachable()

    def secure_installation(self) -> None:
        """Create or reset the admin account through the agent, which grants it to this host's address only."""
        if self.has_valid_credentials():
            return
        self.agent.grant_admin(self.config.admin_user, self.config.root_password)

    def _write_config(self) -> MariaDBMemorySizing:
        """The database VM writes the server's options and sizes it."""
        raise DatabaseError(f"{self.config.host} sizes its own MariaDB server.")

    def ensure_managed_config(self) -> None:
        """The database VM created the variable editor's option file when it set up its server."""

    def has_managed_config(self) -> bool:
        """The database VM always has the variable editor's option file."""
        return True

    def read_managed_config(self) -> str:
        """The text of the variable editor's option file on the database VM."""
        return self.agent.read_managed_config()

    def write_managed_config(self, content: str) -> None:
        """Replace the variable editor's option file on the database VM."""
        self.agent.write_managed_config(content)

    def _total_memory_mb(self) -> int:
        """The database VM's memory in MiB."""
        return self.get_health()["memory_mb"]

    @property
    def memory_share(self) -> float:
        """All of the VM's memory: nothing else runs on it."""
        return 1.0

    def is_reachable(self) -> bool:
        """Whether the admin can log in to the remote server and run a query. The CLI's Python has no
        MySQL driver, so this goes through the client, unlike `is_healthy`."""
        return self.has_valid_credentials()

    def _control(self, action: str) -> None:
        """Run a service action on the database VM."""
        self.agent.control(action, timeout=self.service_timeout)

    def _reset_failed_state(self) -> None:
        """The agent clears a failed state before every service action."""

    def _require_linux_managed_server(self) -> None:
        """Raise unless Pilot manages the remote server. Its VM, not this one, must run Linux."""
        self._require_managed_server()

    def _run_sql_as_superuser(self, sql: str) -> None:
        """Only the database VM has a superuser login, so this host runs SQL as the admin account."""
        raise DatabaseError(f"Pilot has no superuser login to the MariaDB server on {self.config.host}.")

    def get_account_host(self) -> str:
        """The host the admin connection comes from, so Pilot's accounts are no wider than the admin."""
        # --batch prints the column name on the first line.
        lines = self.run_admin_sql("SELECT SUBSTRING_INDEX(USER(), '@', -1);").strip().splitlines()
        if len(lines) < 2 or not lines[-1]:
            raise DatabaseError("MariaDB did not report the host of Pilot's connection.")
        return lines[-1]
