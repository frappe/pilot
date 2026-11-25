from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from pilot.config.common import CommonConfig
from pilot.core.agent.auth import ClientAddress, authorize
from pilot.core.agent.client import API_VERSION
from pilot.core.agent.roles import AgentRole
from pilot.core.agent.roles.database.routes import DatabaseRole
from pilot.core.agent.update import PilotUpdate
from pilot.core.server import Server
from pilot.core.server.monitoring_proc import HostSampler
from pilot.exceptions import BenchError
from pilot.updater import get_installed_version

ROLES: list[type[AgentRole]] = [DatabaseRole]

router = APIRouter()


@router.get("/health")
def health(request: Request, client_address: ClientAddress) -> dict:
    return {
        "api_version": API_VERSION,
        "pilot_version": get_installed_version(),
        "client_address": client_address,
        "roles": {role.name: role.get_health() for role in request.app.state.roles},
    }


@router.get("/metrics")
def metrics() -> dict:
    # Every role's state lives under Pilot's root, so its disk is the one worth watching.
    return HostSampler(Server().root).get_sampled_record()


@router.post("/pilot/update")
def start_pilot_update() -> dict:
    return PilotUpdate().start()


@router.get("/pilot/update")
def read_pilot_update() -> dict:
    return PilotUpdate().get_state()


def refused(request: Request, exc: Exception) -> JSONResponse:
    """A refused operation, with Pilot's reason for the app VM to show."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


def create_app(config: CommonConfig) -> FastAPI:
    """The Pilot agent: fixed operations on this VM for the app VM's Pilot, one group per role, and nothing else."""
    app = FastAPI(title="Pilot agent", dependencies=[Depends(authorize)], openapi_url=None)
    app.state.agent_config = config.agent
    app.state.roles = [role(config) for role in ROLES if role.is_enabled(config)]
    app.add_exception_handler(BenchError, refused)
    app.include_router(router)
    for role in app.state.roles:
        app.include_router(role.router, prefix=f"/{role.name}")
    return app
