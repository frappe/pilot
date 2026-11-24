from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pilot.config.common import CommonConfig
from pilot.core.database.agent.client import API_VERSION
from pilot.exceptions import DatabaseError
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


class ManagedConfig(BaseModel):
    content: str


class AdminAccount(BaseModel):
    user: str
    password: str


def get_manager(request: Request) -> DedicatedMariaDBManager:
    """This VM's MariaDB."""
    return request.app.state.manager


def get_client_address(request: Request) -> str:
    """The caller's address. Every request has one over TCP."""
    if not request.client:
        raise HTTPException(403, "The database agent accepts only the app VM.")
    return request.client.host


Manager = Annotated[DedicatedMariaDBManager, Depends(get_manager)]
ClientAddress = Annotated[str, Depends(get_client_address)]
router = APIRouter()


def authorize(request: Request, client_address: ClientAddress) -> None:
    """Refuse a caller without the token, or one other than the app VM."""
    agent = request.app.state.agent_config
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not agent.token_hash or not hmac.compare_digest(digest, agent.token_hash):
        raise HTTPException(401, "The database agent token is wrong.")
    if not _is_same_address(client_address, agent.client_address):
        raise HTTPException(403, "The database agent accepts only the app VM.")


@router.get("/health")
def health(manager: Manager, client_address: ClientAddress) -> dict:
    is_installed = manager.is_installed()
    return {
        "api_version": API_VERSION,
        "is_installed": is_installed,
        "is_provisioned": manager.is_provisioned(),
        "is_running": manager.is_running(),
        "version": manager.get_server_version() if is_installed else None,
        "memory_mb": manager.host_memory_mb,
        "client_address": client_address,
    }


@router.get("/config/managed")
def read_managed_config(manager: Manager) -> ManagedConfig:
    return ManagedConfig(content=manager.read_managed_config())


@router.put("/config/managed")
def write_managed_config(manager: Manager, body: ManagedConfig) -> None:
    manager.replace_managed_config(body.content)


@router.post("/service/{action}")
def control_service(manager: Manager, action: Literal["start", "stop", "restart"]) -> None:
    manager.control(action)


@router.get("/files")
def read_file(manager: Manager, path: str, tail_bytes: int = 0) -> dict:
    file = manager.get_server_file(path)
    exists = file.path.is_file()
    return {
        "exists": exists,
        "size_bytes": file.size_bytes,
        "text": file.read_tail(tail_bytes) if exists and tail_bytes > 0 else "",
    }


@router.get("/disk")
def disk(manager: Manager) -> dict:
    return {"free_bytes": manager.free_disk_bytes}


@router.post("/accounts/admin")
def grant_admin(manager: Manager, client_address: ClientAddress, body: AdminAccount) -> dict:
    manager.grant_client_admin(body.user, body.password, client_address)
    return {"host": client_address}


def database_error(request: Request, exc: Exception) -> JSONResponse:
    """A refused operation, with Pilot's reason for the app VM to show."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


def create_app(config: CommonConfig) -> FastAPI:
    """The database agent: fixed operations on this VM's MariaDB for the app VM's Pilot, and nothing else."""
    app = FastAPI(title="Pilot database agent", dependencies=[Depends(authorize)], openapi_url=None)
    app.state.agent_config = config.database_agent
    app.state.manager = DedicatedMariaDBManager(config.mariadb)
    app.add_exception_handler(DatabaseError, database_error)
    app.include_router(router)
    return app


def _is_same_address(left: str, right: str) -> bool:
    """Whether two addresses are one host, however each is written."""
    try:
        return ipaddress.ip_address(left) == ipaddress.ip_address(right)
    except ValueError:
        return False
