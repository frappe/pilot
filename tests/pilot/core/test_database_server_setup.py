from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.common import CommonConfig
from pilot.core.database.agent.setup import DatabaseServerSetup
from pilot.exceptions import BenchError
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


@pytest.fixture(autouse=True)
def root(tmp_path: Path):
    with patch("pilot.core.server.Server.root", tmp_path):
        yield tmp_path


@pytest.fixture
def steps(tmp_path: Path):
    """Record the server setup and the agent install instead of running them."""
    calls = MagicMock()
    with (
        patch.object(DedicatedMariaDBManager, "provision", lambda self: calls.provision(self.config)),
        patch(
            "pilot.core.database.agent.setup.AdminEnvManager.install_python_deps", calls.install_python_deps
        ),
        patch("pilot.core.database.agent.setup.user_unit_dir", return_value=tmp_path / "units"),
        patch("pilot.core.database.agent.setup.run_command", calls.run_command),
    ):
        yield calls


def test_the_vm_gets_a_dedicated_server_and_an_agent_for_the_app_vm_only(root: Path, steps) -> None:
    token = DatabaseServerSetup("fdaa:1:2::7").run()

    common = CommonConfig.read(root / "benches")
    assert (common.mariadb.dedicated, common.mariadb.port, len(common.mariadb.root_password)) == (
        True,
        3306,
        32,
    )
    assert common.database_agent.client_address == "fdaa:1:2::7"
    # Only the app VM keeps the token itself.
    assert common.database_agent.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in CommonConfig.path(root / "benches").read_text()
    assert steps.provision.call_args.args[0] == common.mariadb
    unit = (root / "units" / DatabaseServerSetup.unit_name).read_text()
    assert f"ExecStart={root}/.admin-venv/bin/python -m pilot.core.database.agent\n" in unit
    assert steps.run_command.call_args.args[0][-2:] == ["restart", DatabaseServerSetup.unit_name]


def test_a_second_run_keeps_the_server_password_and_changes_the_token(root: Path, steps) -> None:
    first = DatabaseServerSetup("fdaa:1:2::7").run()
    password = CommonConfig.read(root / "benches").mariadb.root_password

    second = DatabaseServerSetup("fdaa:1:2::7").run()

    assert second != first
    assert CommonConfig.read(root / "benches").mariadb.root_password == password


def test_a_client_that_is_not_an_address_is_refused(root: Path, steps) -> None:
    with pytest.raises(BenchError, match="not an IP address"):
        DatabaseServerSetup("app-vm").run()
    assert steps.mock_calls == []


def test_a_vm_with_benches_is_refused(root: Path, steps) -> None:
    with (
        patch("pilot.core.bench.directory.BenchDirectory.iter_configs", return_value=[(root / "main", None)]),
        pytest.raises(BenchError, match="runs benches"),
    ):
        DatabaseServerSetup("fdaa:1:2::7").run()
    assert steps.mock_calls == []
