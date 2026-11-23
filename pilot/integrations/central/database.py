from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pilot.integrations.central.client import CentralClient, CentralClientError
from pilot.integrations.central.metadata import InstanceMetadata

if TYPE_CHECKING:
    from pilot.core.bench import Bench

DATABASE_ATTRIBUTE = "pilot-database"
ACCESS_KEYS = ("host", "port", "ssh_user", "ssh_private_key")


class CentralDatabaseAssignment:
    """The MariaDB host Central creates beside this VM. Pilot takes it over before the credential applies,
    so the credential never lands on a host without a working database."""

    poll_seconds = 5.0

    def __init__(
        self,
        bench: Bench,
        metadata: InstanceMetadata | None = None,
        client: CentralClient | None = None,
    ) -> None:
        self.bench = bench
        self.metadata = metadata or InstanceMetadata()
        self.client = client or CentralClient()

    @property
    def key_path(self) -> Path:
        """Where the SSH key for the database host is kept."""
        return Path.home() / ".ssh" / "pilot-database"

    def apply(self, on_progress: Callable[[str], None] = logging.info) -> bool:
        """Move this host to the assigned database host. False when Central assigned none."""
        if self.bench.config.mariadb.remote or not self.is_assigned():
            return False
        access = self.get_access()
        self.write_key(access["ssh_private_key"])
        self.bench.setup_remote_database(
            host=access["host"],
            ssh_user=access["ssh_user"],
            ssh_key_path=str(self.key_path),
            port=int(access["port"]),
            on_progress=on_progress,
        )
        return True

    def is_assigned(self) -> bool:
        """Whether Central created this VM with its database on another host. Waits for the metadata service,
        since an unreadable attribute must not read as "no remote database"."""
        while self.metadata.get_credentials() is None:
            time.sleep(self.poll_seconds)
        assignment = self.metadata.get_object_attribute(DATABASE_ATTRIBUTE) or {}
        return assignment.get("remote") is True

    def get_access(self) -> dict[str, Any]:
        """The database host's address and SSH login, once Central reports the host running."""
        while (access := self.client.get_database_access())["status"] == "Pending":
            logging.info("Waiting for Central to finish the database host.")
            time.sleep(self.poll_seconds)
        if access["status"] != "Ready":
            raise CentralClientError(
                access.get("error") or f"Central reported the database host {access['status']}."
            )
        if missing := [key for key in ACCESS_KEYS if not access.get(key)]:
            raise CentralClientError(f"Central's database access is missing: {', '.join(missing)}")
        return access

    def write_key(self, private_key: str) -> None:
        """Keep the private key where only this user can read it. ssh refuses a key others can read."""
        from pilot.internal.atomic_file import atomic_write_private_text

        self.key_path.parent.mkdir(mode=0o700, exist_ok=True)
        atomic_write_private_text(self.key_path, private_key.rstrip("\n") + "\n")
