from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from pilot.config.common import CommonConfig
from pilot.core.agent.auth import ClientAddress
from pilot.core.agent.roles import AgentRole
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


class ManagedConfig(BaseModel):
    content: str


class AdminAccount(BaseModel):
    user: str
    password: str


class DatabaseRole(AgentRole):
    """This VM's MariaDB: fixed operations on it for the app VM's Pilot, and nothing else."""

    name = "database"

    @classmethod
    def is_enabled(cls, config: CommonConfig) -> bool:
        """A VM with a dedicated MariaDB is a database server."""
        return config.mariadb.dedicated

    def __init__(self, config: CommonConfig) -> None:
        super().__init__(config)
        self.manager = DedicatedMariaDBManager(config.mariadb)
        self.router = APIRouter()
        self.router.add_api_route("/config/managed", self.read_managed_config, methods=["GET"])
        self.router.add_api_route("/config/managed", self.write_managed_config, methods=["PUT"])
        self.router.add_api_route("/service/{action}", self.control_service, methods=["POST"])
        self.router.add_api_route("/files", self.read_file, methods=["GET"])
        self.router.add_api_route("/disk", self.get_disk, methods=["GET"])
        self.router.add_api_route("/accounts/admin", self.grant_admin, methods=["POST"])

    def get_health(self) -> dict:
        """The server's state."""
        is_installed = self.manager.is_installed()
        return {
            "is_installed": is_installed,
            "is_provisioned": self.manager.is_provisioned(),
            "is_running": self.manager.is_running(),
            "version": self.manager.get_server_version() if is_installed else None,
            "memory_mb": self.manager.host_memory_mb,
        }

    def read_managed_config(self) -> ManagedConfig:
        return ManagedConfig(content=self.manager.read_managed_config())

    def write_managed_config(self, body: ManagedConfig) -> None:
        self.manager.replace_managed_config(body.content)

    def control_service(self, action: Literal["start", "stop", "restart"]) -> None:
        self.manager.control(action)

    def read_file(self, path: str, tail_bytes: int = 0) -> dict:
        file = self.manager.get_server_file(path)
        exists = file.path.is_file()
        return {
            "exists": exists,
            "size_bytes": file.size_bytes,
            "text": file.read_tail(tail_bytes) if exists and tail_bytes > 0 else "",
        }

    def get_disk(self) -> dict:
        return {"free_bytes": self.manager.free_disk_bytes}

    def grant_admin(self, client_address: ClientAddress, body: AdminAccount) -> dict:
        self.manager.grant_client_admin(body.user, body.password, client_address)
        return {"host": client_address}
