from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pilot.integrations.central.client import CentralClient, CentralClientError
from pilot.integrations.central.metadata import InstanceMetadata

if TYPE_CHECKING:
    from pilot.core.bench import Bench

DATABASE_ATTRIBUTE = "pilot-database"
ACCESS_KEYS = ("host", "port", "agent_port", "agent_token")


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

    def apply(self, on_progress: Callable[[str], None] = logging.info) -> bool:
        """Move this host to the assigned database host. False when Central assigned none."""
        if self.bench.config.mariadb.remote or not self.is_assigned():
            return False
        access = self.get_access()
        self.bench.setup_remote_database(
            host=access["host"],
            agent_token=access["agent_token"],
            port=int(access["port"]),
            agent_port=int(access["agent_port"]),
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
        """The database host's address and its database agent's port and token, once Central reports it running."""
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
