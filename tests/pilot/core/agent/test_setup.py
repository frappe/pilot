from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.common import CommonConfig
from pilot.core.agent.roles.database.setup import DatabaseServerSetup
from pilot.core.agent.setup import AgentSetup
from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


@pytest.fixture
def steps(tmp_path: Path):
    """Record the server setup and the agent install instead of running them."""
    calls = MagicMock()
    with (
        patch.object(DedicatedMariaDBManager, "provision", lambda self: calls.provision(self.config)),
        patch("pilot.core.agent.setup.AdminEnvManager.install_python_deps", calls.install_python_deps),
        patch("pilot.core.agent.setup.user_unit_dir", return_value=tmp_path / "units"),
        patch("pilot.core.agent.setup.run_command", calls.run_command),
    ):
        yield calls


def test_the_vm_gets_a_dedicated_server_and_an_agent_for_the_app_vm_only(root: Path, steps) -> None:
    assert not Server().has_agent
    token = DatabaseServerSetup("fdaa:1:2::7").run()

    assert Server().has_agent

    common = CommonConfig.read(root / "benches")
    assert (common.mariadb.dedicated, common.mariadb.port, len(common.mariadb.root_password)) == (
        True,
        3306,
        32,
    )
    assert common.agent.client_address == "fdaa:1:2::7"
    # Only the app VM keeps the token itself.
    assert common.agent.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in CommonConfig.path(root / "benches").read_text()
    assert steps.provision.call_args.args[0] == common.mariadb
    unit = (root / "units" / AgentSetup.unit_name).read_text()
    assert f"ExecStart={root}/.admin-venv/bin/python -m pilot.core.agent\n" in unit
    assert steps.run_command.call_args.args[0][-2:] == ["restart", AgentSetup.unit_name]


def test_a_second_run_keeps_the_server_password_and_changes_the_token(root: Path, steps) -> None:
    first = DatabaseServerSetup("fdaa:1:2::7").run()
    password = CommonConfig.read(root / "benches").mariadb.root_password

    second = DatabaseServerSetup("fdaa:1:2::7").run()

    assert second != first
    assert CommonConfig.read(root / "benches").mariadb.root_password == password


@pytest.mark.parametrize(
    ("client_address", "benches", "message"),
    [
        pytest.param("app-vm", [], "not an IP address", id="client-not-an-address"),
        pytest.param("fdaa:1:2::7", ["main"], "runs benches", id="vm-with-benches"),
    ],
)
def test_an_unusable_client_or_vm_is_refused_before_any_step(
    root: Path, steps, client_address: str, benches: list[str], message: str
) -> None:
    with (
        patch(
            "pilot.core.bench.directory.BenchDirectory.iter_configs",
            return_value=[(root / name, None) for name in benches],
        ),
        pytest.raises(BenchError, match=message),
    ):
        DatabaseServerSetup(client_address).run()
    assert steps.mock_calls == []


# runpy warns that the package already imported this module, as the test needs.
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_an_update_reinstalls_and_restarts_the_agent_from_the_saved_config(root: Path, steps) -> None:
    import runpy

    DatabaseServerSetup("fdaa:1:2::7", agent_port=7790).run()
    unit = root / "units" / AgentSetup.unit_name
    unit.unlink()

    with (
        patch("pilot.internal.command.run_command", steps.rerun),
        patch("pilot.managers.systemd_user.user_unit_dir", return_value=root / "units"),
    ):
        # The updater runs this module in a new process, which holds the new code.
        runpy.run_module("pilot.core.agent.setup", run_name="__main__")

    assert unit.is_file()
    assert steps.rerun.call_args.args[0][-2:] == ["restart", AgentSetup.unit_name]
    steps.provision.assert_called_once()
