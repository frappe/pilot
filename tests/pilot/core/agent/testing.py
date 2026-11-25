from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from pilot.config import MariaDBConfig
from pilot.config.agent import AgentConfig
from pilot.config.common import CommonConfig
from pilot.core.agent.app import create_app

APP_VM = "fdaa:1:2::7"
TOKEN = "token"


def make_client(address: str = APP_VM, token: str = TOKEN, *, is_database_server: bool = True) -> TestClient:
    """A caller of the agent of a VM that is a database server, or has no role."""
    config = CommonConfig(
        mariadb=MariaDBConfig(port=3306, dedicated=is_database_server),
        agent=AgentConfig(token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), client_address=APP_VM),
    )
    return TestClient(
        create_app(config), client=(address, 50000), headers={"Authorization": f"Bearer {token}"}
    )
