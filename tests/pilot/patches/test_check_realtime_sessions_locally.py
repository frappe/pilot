from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pilot.core.bench import Bench
from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.patches.check_realtime_sessions_locally import check_sessions_locally, run
from tests.pilot.commands.test_commands import make_bench


def test_points_realtime_at_local_web_once(tmp_path: Path) -> None:
    bench_dir = tmp_path / "main"
    (bench_dir / "sites").mkdir(parents=True)
    (bench_dir / "bench.toml").write_text('[bench]\nname = "main"\npython = "3.14"\n')
    config_path = bench_dir / "sites" / "common_site_config.json"
    config_path.write_text('{"webserver_port": 8000}')

    run(tmp_path)
    assert json.loads(config_path.read_text()) == {"webserver_port": 8000, "webserver_host": "127.0.0.1"}

    config_path.write_text('{"webserver_host": "example.com"}')
    run(tmp_path)
    assert json.loads(config_path.read_text())["webserver_host"] == "example.com"


@pytest.fixture
def production_bench(tmp_path: Path):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.production.enabled = True
    (bench.sites_path / "common_site_config.json").write_text("{}")
    return bench


@pytest.mark.parametrize(("is_running", "is_lite_mode", "restarts"), [(True, False, True), (False, False, False), (True, True, False)])
def test_restarts_a_running_production_socketio(production_bench, is_running, is_lite_mode, restarts) -> None:
    manager = MagicMock(python=Path("/usr/bin/python3"))
    manager.is_running.return_value = is_running
    with (
        patch.object(ProcessManager, "for_bench", return_value=manager),
        patch.object(Bench, "is_lite_mode", new_callable=PropertyMock, return_value=is_lite_mode),
    ):
        check_sessions_locally(production_bench)

    assert manager.restart_processes.called is restarts
    if restarts:
        assert [definition.name for definition in manager.restart_processes.call_args.args[0]] == ["socketio"]


def test_a_broken_app_declaration_does_not_block_the_patch(production_bench) -> None:
    app = production_bench.apps_path / "mail"
    (app / ".git").mkdir(parents=True)
    (app / "pyproject.toml").write_text("[tool.pilot\n")
    manager = SystemdProcessManager(production_bench)
    with (
        patch.object(ProcessManager, "for_bench", return_value=manager),
        patch.object(manager, "is_running", return_value=True),
        patch.object(manager, "restart_processes") as restart,
    ):
        check_sessions_locally(production_bench)

    restart.assert_called_once()
