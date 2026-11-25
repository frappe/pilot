from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import MariaDBConfig
from pilot.config.common import CommonConfig
from pilot.core.agent.client import API_VERSION, AgentClient
from pilot.core.agent.roles.database.client import DatabaseAgentClient
from pilot.core.server import Server
from pilot.exceptions import AgentError, AgentRefusedError, DatabaseError
from tests.pilot.core.agent.testing import TOKEN


def _answers(*answers):
    """An agent client whose requests return or raise these answers in order."""
    agent = AgentClient("fdaa:1:2::9", 7781, TOKEN)
    agent.pilot_update_poll_interval = 0
    return agent, patch.object(AgentClient, "request", side_effect=answers)


def test_the_app_vm_waits_for_the_update_through_the_agent_restart() -> None:
    running = {"status": "running", "error": ""}
    done = {"status": "succeeded", "error": "", "version_before": "dev 1", "version_after": "dev 2"}
    agent, requests = _answers(running, AgentError("Pilot cannot reach the Pilot agent"), done)
    progress = []

    with requests as request:
        assert agent.update_pilot(on_progress=progress.append) == done

    assert [call.args for call in request.call_args_list] == [
        ("POST", "/pilot/update"),
        ("GET", "/pilot/update"),
        ("GET", "/pilot/update"),
    ]
    assert progress[-1] == "Pilot on fdaa:1:2::9 updated: dev 1 -> dev 2."


@pytest.mark.parametrize(
    ("answers", "timeout", "message"),
    [
        (
            ({"status": "running"}, {"status": "failed", "error": "git pull failed."}),
            60,
            "update: git pull failed.",
        ),
        ((AgentRefusedError("refused: Not Found", 404),), 60, "too old to update itself"),
        (({"status": "running"},), 0, "did not finish updating in 0 minutes"),
    ],
)
def test_the_app_vm_fails_with_the_reason_when_the_update_does_not_succeed(answers, timeout, message) -> None:
    agent, requests = _answers(*answers)
    agent.pilot_update_timeout = timeout

    with requests, pytest.raises(AgentError, match=message):
        agent.update_pilot()


def _database_client() -> DatabaseAgentClient:
    return DatabaseAgentClient.for_config(MariaDBConfig(host="fdaa:1:2::9", remote=True, agent_token=TOKEN))


@pytest.mark.parametrize(
    ("roles", "message"),
    [
        pytest.param({}, "The VM fdaa:1:2::9 is not a database server.", id="no-database-role"),
        pytest.param(None, "speaks API version 0", id="other-api-version"),
    ],
)
def test_the_database_client_refuses_a_vm_that_does_not_serve_it(roles: dict | None, message: str) -> None:
    health = {"api_version": API_VERSION, "roles": roles} if roles is not None else {"api_version": 0}

    with (
        patch.object(AgentClient, "request", return_value=health),
        pytest.raises(DatabaseError, match=message),
    ):
        _database_client().get_health()


def test_the_database_client_calls_the_database_requests_and_reports_failures_as_database_errors() -> None:
    database = _database_client()

    with patch.object(AgentClient, "request", return_value={"free_bytes": 5}) as request:
        assert database.get_free_disk_bytes() == 5
    request.assert_called_once_with("GET", "/database/disk", None, None)

    refused = AgentRefusedError("The Pilot agent on fdaa:1:2::9 refused: no", 422)
    with patch.object(AgentClient, "request", side_effect=refused), pytest.raises(DatabaseError) as raised:
        database.grant_admin("pilot", "secret")
    assert raised.value.__cause__ is refused


@pytest.mark.parametrize(
    ("mariadb", "agents"),
    [
        pytest.param(MariaDBConfig(), [], id="local-mariadb"),
        pytest.param(
            MariaDBConfig(host="fdaa:1:2::9", remote=True, agent_port=7790, agent_token=TOKEN),
            [("fdaa:1:2::9", 7790, TOKEN)],
            id="remote-mariadb",
        ),
    ],
)
def test_a_host_manages_the_agent_of_its_remote_database_vm(
    root: Path, mariadb: MariaDBConfig, agents: list[tuple]
) -> None:
    (root / "benches").mkdir()
    CommonConfig(mariadb=mariadb).write(root / "benches")

    assert [(agent.host, agent.port, agent.token) for agent in Server().agents] == agents
