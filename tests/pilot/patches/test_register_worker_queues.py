from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pilot.config import BenchConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.internal.patch_state import is_applied
from pilot.managers.processes.local import ProcessManager
from pilot.patches.register_worker_queues import (
    PATCH_NAME,
    import_queue_timeouts,
    register_worker_queues,
    run,
)
from tests.pilot.commands.test_commands import make_bench


def _bench_dir(tmp_path: Path, workers: str, common_site_config: dict) -> Path:
    bench_dir = tmp_path / "main"
    (bench_dir / "sites").mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "main"\npython = "3.14"\n\n{workers}')
    (bench_dir / "sites" / "common_site_config.json").write_text(json.dumps(common_site_config))
    return bench_dir


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_lists_worker_queues_once(tmp_path: Path) -> None:
    bench_dir = _bench_dir(
        tmp_path, '[[workers]]\nqueues = ["default", "guard-review"]\ncount = 2\n', {"webserver_port": 8000}
    )
    config_path = bench_dir / "sites" / "common_site_config.json"

    run(tmp_path)
    assert _read_json(config_path) == {"webserver_port": 8000, "workers": {"guard-review": {}}}

    config_path.write_text("{}")
    run(tmp_path)
    assert _read_json(config_path) == {}


def test_hand_set_timeouts_move_to_bench_toml(tmp_path: Path) -> None:
    workers = (
        '[[workers]]\nqueues = ["default", "long"]\ncount = 2\n\n'
        '[[workers]]\nqueues = ["guard-review"]\ncount = 1\n\n'
        '[[workers]]\nqueues = ["guard-base"]\ncount = 1\n'
    )
    bench_dir = _bench_dir(
        tmp_path,
        workers,
        {"workers": {"long": {"timeout": 3000}, "guard-review": {"timeout": 900}, "app": {}}},
    )

    run(tmp_path)

    assert [group.timeout for group in BenchConfig.read(bench_dir).workers.groups] == [3000, 900, None]
    assert _read_json(bench_dir / "sites" / "common_site_config.json")["workers"] == {
        "default": {"timeout": 3000},
        "long": {"timeout": 3000},
        "guard-review": {"timeout": 900},
        "guard-base": {},
        "app": {},
    }


def test_an_imported_timeout_covers_frappes_defaults(tmp_path: Path) -> None:
    bench_dir = _bench_dir(
        tmp_path,
        '[[workers]]\nqueues = ["default", "short", "long"]\ncount = 1\n',
        {"workers": {"default": {"timeout": 600}}},
    )

    import_queue_timeouts(bench_dir)

    assert BenchConfig.read(bench_dir).workers.groups[0].timeout == 1500


def test_conflicting_hand_set_timeouts_are_not_imported(tmp_path: Path, capsys) -> None:
    workers = '[[workers]]\nqueues = ["default"]\ncount = 1\n\n[[workers]]\nqueues = ["default", "long"]\ncount = 1\n'
    bench_dir = _bench_dir(
        tmp_path, workers, {"workers": {"default": {"timeout": 600}, "long": {"timeout": 3000}}}
    )

    assert import_queue_timeouts(bench_dir) is False

    assert [group.timeout for group in BenchConfig.read(bench_dir).workers.groups] == [None, None]
    assert "set the worker timeouts in Settings again" in capsys.readouterr().out


def test_a_timeout_conflict_leaves_the_bench_for_the_next_update(tmp_path: Path) -> None:
    workers = '[[workers]]\nqueues = ["default"]\ncount = 1\n\n[[workers]]\nqueues = ["default", "long"]\ncount = 1\n'
    bench_dir = _bench_dir(
        tmp_path, workers, {"workers": {"default": {"timeout": 600}, "long": {"timeout": 3000}}}
    )
    config_path = bench_dir / "sites" / "common_site_config.json"
    before = config_path.read_text()

    run(tmp_path)

    assert config_path.read_text() == before
    assert [group.timeout for group in BenchConfig.read(bench_dir).workers.groups] == [None, None]
    assert not is_applied(bench_dir, PATCH_NAME)


@pytest.fixture
def production_bench(tmp_path: Path):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.production.enabled = True
    bench.config.production.process_manager = "systemd"
    bench.config.workers.groups.append(WorkerGroup(queues=["guard-review"], count=1))
    (bench.sites_path / "common_site_config.json").write_text("{}")
    return bench


_SYSTEMD_POOLS = {"worker_pool_default", "worker_pool_short", "worker_pool_long", "worker_pool_guard_review"}
_SUPERVISOR_WORKERS = {
    "worker_default_1",
    "worker_default_2",
    "worker_short_1",
    "worker_long_1",
    "worker_guard_review_1",
}


def _register(bench, is_running: bool = True, is_lite_mode: bool = False) -> MagicMock:
    manager = MagicMock(python=Path("/usr/bin/python3"))
    manager.is_running.return_value = is_running
    common_site_config = bench.sites_path / "common_site_config.json"
    manager.restart_processes.side_effect = lambda definitions: json.loads(common_site_config.read_text())[
        "workers"
    ]
    with (
        patch.object(ProcessManager, "detect_running", return_value=manager),
        patch.object(Bench, "is_lite_mode", new_callable=PropertyMock, return_value=is_lite_mode),
    ):
        register_worker_queues(bench)
    return manager


def _restarted(manager: MagicMock) -> set[str]:
    return {definition.name for definition in manager.restart_processes.call_args.args[0]}


@pytest.mark.parametrize(
    ("process_manager", "is_lite_mode", "restarted"),
    [
        ("systemd", False, _SYSTEMD_POOLS),
        ("supervisor", False, _SUPERVISOR_WORKERS),
        ("systemd", True, {"web"}),
    ],
)
def test_restarts_the_processes_that_run_jobs(
    production_bench, process_manager, is_lite_mode, restarted
) -> None:
    production_bench.config.production.process_manager = process_manager

    manager = _register(production_bench, is_lite_mode=is_lite_mode)

    assert _restarted(manager) == restarted


@pytest.mark.parametrize("is_production", [True, False])
def test_a_stopped_or_dev_bench_restarts_nothing(production_bench, is_production) -> None:
    production_bench.config.production.enabled = is_production

    manager = _register(production_bench, is_running=False)

    manager.restart_processes.assert_not_called()
    config = json.loads((production_bench.sites_path / "common_site_config.json").read_text())
    assert config["workers"] == {"guard-review": {}}


def test_an_unchanged_queue_list_restarts_nothing(production_bench) -> None:
    (production_bench.sites_path / "common_site_config.json").write_text('{"workers": {"guard-review": {}}}')

    manager = _register(production_bench)

    manager.restart_processes.assert_not_called()


def test_a_malformed_hand_set_timeout_is_reported(tmp_path: Path, capsys) -> None:
    bench_dir = _bench_dir(
        tmp_path, '[[workers]]\nqueues = ["long"]\ncount = 1\n', {"workers": {"long": {"timeout": "3000"}}}
    )

    import_queue_timeouts(bench_dir)

    assert BenchConfig.read(bench_dir).workers.groups[0].timeout is None
    assert "set the timeout of queue 'long' in Settings again (got '3000')" in capsys.readouterr().out
