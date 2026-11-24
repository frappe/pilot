from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.integrations.central.client import CentralClientError
from pilot.integrations.central.database import CentralDatabaseAssignment
from pilot.integrations.central.metadata import InstanceMetadata

READY = {
    "status": "Ready",
    "host": "fdaa:1:2::3",
    "port": 3306,
    "ssh_user": "ubuntu",
    "memory_mib": 8192,
    "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nkey\n-----END OPENSSH PRIVATE KEY-----",
}


def _metadata(database: str | None, credentials: list | None = None) -> MagicMock:
    metadata = MagicMock(spec=InstanceMetadata)
    metadata.get_credentials.side_effect = credentials or [{"central_endpoint": "https://central"}]
    metadata.get_object_attribute.side_effect = lambda name: json.loads(database) if database else None
    return metadata


def _assignment(tmp_path: Path, database: str | None, *answers: dict, remote: bool = False):
    bench = MagicMock()
    bench.config.mariadb.remote = remote
    client = MagicMock()
    client.get_database_access.side_effect = list(answers)
    assignment = CentralDatabaseAssignment(bench, _metadata(database), client)
    assignment.poll_seconds = 0
    return assignment, bench, client


@pytest.fixture(autouse=True)
def home(tmp_path: Path):
    with patch("pilot.integrations.central.database.Path.home", return_value=tmp_path):
        yield tmp_path


def test_an_assigned_database_host_is_taken_over_with_the_key_central_gives(tmp_path: Path) -> None:
    assignment, bench, client = _assignment(tmp_path, '{"remote": true}', {"status": "Pending"}, READY)

    assert assignment.apply() is True

    key = tmp_path / ".ssh" / "pilot-database"
    assert key.read_text() == READY["ssh_private_key"] + "\n"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert client.get_database_access.call_count == 2
    bench.setup_remote_database.assert_called_once()
    assert bench.setup_remote_database.call_args.kwargs | {"on_progress": None} == {
        "host": "fdaa:1:2::3",
        "ssh_user": "ubuntu",
        "ssh_key_path": str(key),
        "memory_mb": 8192,
        "port": 3306,
        "on_progress": None,
    }


@pytest.mark.parametrize("database", [None, '{"remote": false}'])
def test_a_vm_without_a_database_host_keeps_its_local_server(tmp_path: Path, database: str | None) -> None:
    assignment, bench, client = _assignment(tmp_path, database)

    assert assignment.apply() is False

    client.get_database_access.assert_not_called()
    bench.setup_remote_database.assert_not_called()


def test_the_flag_is_read_only_once_the_metadata_service_answers(tmp_path: Path) -> None:
    assignment = CentralDatabaseAssignment(
        MagicMock(), _metadata('{"remote": true}', [None, None, {}]), MagicMock()
    )
    assignment.poll_seconds = 0

    assert assignment.is_assigned() is True
    assert assignment.metadata.get_credentials.call_count == 3


def test_a_host_already_on_a_remote_server_is_left_alone(tmp_path: Path) -> None:
    assignment, bench, client = _assignment(tmp_path, '{"remote": true}', READY, remote=True)

    assert assignment.apply() is False
    client.get_database_access.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"status": "Failed", "error": "The database host could not be created."}, "could not be created"),
        ({**READY, "ssh_private_key": ""}, "missing: ssh_private_key"),
        ({key: value for key, value in READY.items() if key != "memory_mib"}, "missing: memory_mib"),
    ],
)
def test_a_failed_or_incomplete_answer_stops_the_boot(tmp_path: Path, answer: dict, message: str) -> None:
    assignment, bench, _ = _assignment(tmp_path, '{"remote": true}', answer)

    with pytest.raises(CentralClientError, match=message):
        assignment.apply()
    bench.setup_remote_database.assert_not_called()
    assert not (tmp_path / ".ssh" / "pilot-database").exists()
