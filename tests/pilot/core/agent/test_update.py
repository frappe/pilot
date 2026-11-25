from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot.core.agent.update import PilotUpdate
from pilot.exceptions import CommandError
from tests.pilot.core.agent.testing import make_client


@pytest.fixture
def pilot_update(root: Path):
    """The update's launch and unit state, recorded instead of run."""
    with (
        patch("pilot.core.agent.update.run_command") as launch,
        patch.object(PilotUpdate, "is_unit_active", new_callable=PropertyMock, return_value=True) as active,
        patch("pilot.core.agent.update.get_installed_version", return_value="dev 1111111"),
    ):
        yield launch, active


def test_a_pilot_update_runs_outside_the_agent_and_a_second_start_joins_it(pilot_update) -> None:
    launch, active = pilot_update
    client = make_client(is_database_server=False)

    first = client.post("/pilot/update").json()
    second = client.post("/pilot/update").json()

    assert first == second == client.get("/pilot/update").json()
    assert (first["status"], first["version_before"]) == ("running", "dev 1111111")
    launch.assert_called_once()
    command = launch.call_args.args[0]
    assert command[:3] == ["systemd-run", "--user", "--unit=pilot-agent-update.service"]
    assert command[-3:] == ["-P", "-m", "pilot.core.agent.update"]

    # A run whose unit is gone, for example after a reboot, has failed and does not block a new one.
    active.return_value = False
    assert "stopped before it finished" in client.get("/pilot/update").json()["error"]
    launch.side_effect = CommandError("systemd-run failed")
    refused = client.post("/pilot/update")
    assert refused.status_code == 422
    assert client.get("/pilot/update").json()["error"] == "systemd-run failed"


def test_the_update_records_its_result_for_the_app_vm(pilot_update) -> None:
    update = PilotUpdate()
    update.start()
    with patch("pilot.core.agent.update.perform_upgrade"):
        update.run()
    assert update.get_state()["status"] == "succeeded"

    update.start()
    with (
        patch("pilot.core.agent.update.perform_upgrade", side_effect=CommandError("git pull failed.")),
        pytest.raises(CommandError),
    ):
        update.run()
    state = make_client(is_database_server=False).get("/pilot/update").json()
    assert (state["status"], state["version_after"]) == ("failed", "dev 1111111")
    assert state["error"] == f"git pull failed. See {update.log_path} on that VM."
