from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pilot.commands.tasks.start import StartTaskWorkerCommand
from pilot.commands.tasks.status import TaskWorkerStatusCommand
from pilot.commands.tasks.stop import StopTaskWorkerCommand
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStatus, WorkerStore


def bench(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(path=tmp_path)


def test_tasks_start_persists_running_intent(
    tmp_path: Path,
    capsys,
) -> None:
    StartTaskWorkerCommand(bench(tmp_path)).run()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.RUNNING
    assert capsys.readouterr().out == "Task worker start requested.\n"


def test_tasks_stop_persists_stopped_intent(
    tmp_path: Path,
    capsys,
) -> None:
    StopTaskWorkerCommand(bench(tmp_path)).run()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.STOPPED
    assert capsys.readouterr().out == "Task worker will stop after its current task.\n"


def test_tasks_status_reports_intent_and_current_task(
    tmp_path: Path,
    capsys,
) -> None:
    store = WorkerStore(tmp_path)
    store.write_intent(WorkerIntent.STOPPED)
    store.write_state(WorkerStatus.DRAINING, 4321, "task-id")

    TaskWorkerStatusCommand(bench(tmp_path)).run()

    assert capsys.readouterr().out == (
        "Task worker: draining (desired: stopped)\n"
        "Current task: task-id\n"
        "Task activity: active (queued: 0, running: 0)\n"
    )
