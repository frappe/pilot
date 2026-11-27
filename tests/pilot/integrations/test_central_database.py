from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pilot.integrations.central.client import CentralClientError
from pilot.integrations.central.database import CentralDatabaseAssignment
from pilot.integrations.central.metadata import InstanceMetadata

READY = {
    "status": "Ready",
    "host": "fdaa:1:2::3",
    "port": 3306,
    "agent_port": 7781,
    "agent_token": "token",
}


def _metadata(database: str | None, credentials: list | None = None) -> MagicMock:
    metadata = MagicMock(spec=InstanceMetadata)
    metadata.get_credentials.side_effect = credentials or [{"central_endpoint": "https://central"}]
    metadata.get_object_attribute.side_effect = lambda name: json.loads(database) if database else None
    return metadata


def _assignment(database: str | None, *answers: dict, remote: bool = False):
    bench = MagicMock()
    bench.config.mariadb.remote = remote
    client = MagicMock()
    client.get_database_access.side_effect = list(answers)
    assignment = CentralDatabaseAssignment(bench, _metadata(database), client)
    assignment.poll_seconds = 0
    return assignment, bench, client


def test_an_assigned_database_host_is_taken_over_with_the_agent_token_central_gives() -> None:
    assignment, bench, client = _assignment('{"remote": true}', {"status": "Pending"}, READY)

    assert assignment.apply() is True

    assert client.get_database_access.call_count == 2
    bench.setup_remote_database.assert_called_once()
    assert bench.setup_remote_database.call_args.kwargs | {"on_progress": None} == {
        "host": "fdaa:1:2::3",
        "agent_token": "token",
        "port": 3306,
        "agent_port": 7781,
        "on_progress": None,
    }


@pytest.mark.parametrize(
    ("database", "remote"),
    [
        pytest.param(None, False, id="no-attribute"),
        pytest.param('{"remote": false}', False, id="not-assigned"),
        pytest.param('{"remote": true}', True, id="already-remote"),
    ],
)
def test_a_vm_without_a_new_database_host_is_left_alone(database: str | None, remote: bool) -> None:
    assignment, bench, client = _assignment(database, READY, remote=remote)

    assert assignment.apply() is False

    client.get_database_access.assert_not_called()
    bench.setup_remote_database.assert_not_called()


def test_the_flag_is_read_only_once_the_metadata_service_answers() -> None:
    assignment = CentralDatabaseAssignment(
        MagicMock(), _metadata('{"remote": true}', [None, None, {}]), MagicMock()
    )
    assignment.poll_seconds = 0

    assert assignment.is_assigned() is True
    assert assignment.metadata.get_credentials.call_count == 3


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"status": "Failed", "error": "The database host could not be created."}, "could not be created"),
        ({**READY, "agent_token": ""}, "missing: agent_token"),
        ({key: value for key, value in READY.items() if key != "agent_port"}, "missing: agent_port"),
    ],
)
def test_a_failed_or_incomplete_answer_stops_the_boot(answer: dict, message: str) -> None:
    assignment, bench, _ = _assignment('{"remote": true}', answer)

    with pytest.raises(CentralClientError, match=message):
        assignment.apply()
    bench.setup_remote_database.assert_not_called()
