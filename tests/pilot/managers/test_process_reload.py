"""Tests for reloading the dev bench's web process without touching admin."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import ANY

from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.local import ProcessManager
from tests.pilot.managers.test_managers_extra import make_bench


def _manager(tmp_path: Path, monkeypatch) -> ProcessManager:
    bench = make_bench(tmp_path)
    bench.pids_path.mkdir(parents=True, exist_ok=True)
    manager = ProcessManager(bench)
    monkeypatch.setattr(manager, "_clear_frappe_cache", lambda: None)
    return manager


def test_reload_workers_is_a_noop_when_the_bench_is_stopped(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    manager.reload_workers(web_only=True)

    assert not manager.reload_request_file.exists()


def test_reload_workers_queues_a_web_request(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "is_running", lambda: True)

    manager.reload_workers(web_only=True)

    assert manager.reload_request_file.read_text() == "web"


def _definition(name: str) -> ProcessDefinition:
    return ProcessDefinition(name=name, argv=["true"], log_file=Path("/dev/null"))


def test_reload_request_restarts_web_but_not_admin(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    definitions = {name: _definition(name) for name in ("web", "admin")}
    manager._procs = {name: subprocess.Popen(["sleep", "30"]) for name in definitions}
    originals = {name: proc.pid for name, proc in manager._procs.items()}

    terminated: list[int] = []
    monkeypatch.setattr(manager, "_terminate", lambda proc: terminated.append(proc.pid))
    monkeypatch.setattr(manager, "_spawn", lambda pd: manager._procs.__setitem__(pd.name, "restarted"))

    manager.reload_request_file.write_text("web")
    manager._apply_reload_request(definitions)

    assert terminated == [originals["web"]]
    assert manager._procs["web"] == "restarted"
    assert manager._procs["admin"].pid == originals["admin"]
    assert not manager.reload_request_file.exists()

    for proc in manager._procs.values():
        if isinstance(proc, subprocess.Popen):
            proc.kill()


def test_workload_reload_skips_admin_and_redis(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    names = ("web", "socketio", "worker_default_1", "admin", "admin-ui", "redis_cache", "redis_queue")
    definitions = {name: _definition(name) for name in names}
    manager._procs = dict.fromkeys(definitions, "running")

    restarted: list[str] = []
    monkeypatch.setattr(manager, "_terminate", lambda proc: None)
    monkeypatch.setattr(manager, "_spawn", lambda pd: restarted.append(pd.name))

    manager.reload_request_file.write_text("workload")
    manager._apply_reload_request(definitions)

    assert restarted == ["web", "socketio", "worker_default_1"]


def test_tasks_that_change_bench_apps_declare_a_single_reload_callback() -> None:
    """One reload per task, not one per app - installing an app plus its
    dependencies must not restart the workload several times."""
    from unittest.mock import MagicMock

    from pilot.tasks.callbacks import task_callbacks_for
    from pilot.tasks.get_app import GetAppTask
    from pilot.tasks.remove_app import RemoveAppTask
    from pilot.tasks.switch_branch import SwitchBranchTask

    tasks = [
        GetAppTask(bench=MagicMock(), bench_root=Path("/tmp/bench"), repo="lms"),
        RemoveAppTask(bench=MagicMock(), bench_root=Path("/tmp/bench"), name="lms"),
        SwitchBranchTask(bench=MagicMock(), bench_root=Path("/tmp/bench"), name="lms", branch="main"),
    ]

    for task in tasks:
        assert task_callbacks_for(task)["on_success"] == {
            "operation": "reload-workers",
            "args": {"web_only": False},
        }


def test_site_app_tasks_clear_the_site_cache_instead_of_reloading() -> None:
    """The app is already importable, so a restart buys nothing over a cache clear."""
    from unittest.mock import MagicMock

    from pilot.tasks.callbacks import task_callbacks_for
    from pilot.tasks.install_app import InstallAppTask
    from pilot.tasks.uninstall_app import UninstallAppTask

    for task in (
        InstallAppTask(bench=MagicMock(), bench_root=Path("/tmp/bench"), site="a.local", app="lms"),
        UninstallAppTask(bench=MagicMock(), bench_root=Path("/tmp/bench"), site="a.local", app="lms"),
    ):
        assert "on_success" not in task_callbacks_for(task)


def test_reload_workers_callback_is_registered() -> None:
    from pilot.internal.tasks.callbacks import validate_callback

    assert validate_callback({"operation": "reload-workers", "args": {"web_only": False}}) == {
        "operation": "reload-workers",
        "args": {"web_only": False},
    }


def test_reload_workers_callback_reloads_the_bench(tmp_path: Path, monkeypatch) -> None:
    from pilot.core.bench import Bench
    from pilot.internal.tasks.callbacks import run_callback

    make_bench(tmp_path).config.write(tmp_path)
    scopes: list[bool] = []
    monkeypatch.setattr(Bench, "reload_workers", lambda self, web_only=False: scopes.append(web_only))

    run_callback({"operation": "reload-workers", "args": {"web_only": False}}, {"bench_root": str(tmp_path)})

    assert scopes == [False]


def test_get_and_install_reloads_before_installing_on_the_site(tmp_path: Path) -> None:
    """install-app enqueues jobs importing the new app, so the workers have to
    restart first - reloading only after the task leaves them crashing on import."""
    from unittest.mock import MagicMock, call, patch

    from pilot.tasks.get_and_install_app import GetAndInstallAppTask

    task = GetAndInstallAppTask(
        bench=MagicMock(), bench_root=tmp_path, marketplace_app="erpnext", site="a.local"
    )
    order = MagicMock()
    task.bench.reload_workers = order.reload

    with (
        patch.object(task, "fetch", return_value=MagicMock()),
        patch.object(task, "install_on_sites", order.install),
    ):
        task.run()

    assert order.mock_calls == [call.reload(), call.install(ANY)]


def test_stop_all_stops_redis_after_the_workload(tmp_path: Path, monkeypatch) -> None:
    """Redis outlives its clients; killing it first makes them log a lost connection."""
    manager = _manager(tmp_path, monkeypatch)
    manager._procs = dict.fromkeys(("web", "redis_cache", "admin", "redis_queue"), "running")

    waves: list[list[str]] = []
    monkeypatch.setattr(manager, "_stop_group", lambda names: waves.append(names))

    manager._stop_all()

    assert waves == [["web", "admin"], ["redis_cache", "redis_queue"]]
