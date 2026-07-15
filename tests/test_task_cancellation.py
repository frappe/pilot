from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from admin.backend.tasks.manager.process_identity import ProcessInspector
from admin.backend.tasks.manager.task_process import TaskProcess, TaskProcessRecord
from admin.backend.tasks.manager.task_runner import TaskRunner
from admin.backend.tasks.manager.task_state import TaskStatus
from admin.backend.tasks.manager.task_store import TaskStore
from pilot.exceptions import TaskNotRunningError

TASK_ID = "20260715-120000-aabbcc"


def create_running_task(bench_root: Path) -> TaskStore:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": TASK_ID,
            "command": "build",
            "args": {},
            "command_argv": [sys.executable, "-c", "pass"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        },
        {
            "secrets.json": '{"admin_password":"secret"}',
            "callbacks.json": '{"on_success":null}',
        },
    )
    assert store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    return store


def start_owned_process(
    store: TaskStore,
    argv: list[str],
    launch_id: str = "cancellation-test",
) -> subprocess.Popen:
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env={**os.environ, "BENCH_TASK_LAUNCH_ID": launch_id},
    )
    identity = ProcessInspector().capture(process.pid, argv, launch_id)
    record = TaskProcessRecord(TASK_ID, argv, identity)
    store.write_process(TASK_ID, process.pid, record.to_dict())
    return process


def stop_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def pid_is_running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return state != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_cancel_terminates_only_the_verified_task_group(tmp_path: Path) -> None:
    store = create_running_task(tmp_path)
    task = start_owned_process(
        store,
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )

    try:
        TaskProcess(tmp_path).cancel(TASK_ID, grace_seconds=0.1)
        task.wait(timeout=5)

        assert store.read_status(TASK_ID) == TaskStatus.KILLED
        assert store.read_process(TASK_ID) is None
        assert not (store.task_dir(TASK_ID) / "secrets.json").exists()
        assert not (store.task_dir(TASK_ID) / "callbacks.json").exists()
        assert unrelated.poll() is None
    finally:
        stop_process_group(task)
        stop_process_group(unrelated)


def test_cancel_force_kills_a_term_resistant_task_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    code = (
        "import signal,subprocess,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    argv = [sys.executable, "-c", code, str(child_pid_path)]
    store = create_running_task(tmp_path)
    task = start_owned_process(store, argv)

    try:
        deadline = time.monotonic() + 5
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(child_pid_path.read_text())

        TaskProcess(tmp_path).cancel(TASK_ID, grace_seconds=0.1)
        task.wait(timeout=5)

        deadline = time.monotonic() + 5
        while pid_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not pid_is_running(child_pid)
        assert store.read_status(TASK_ID) == TaskStatus.KILLED
        assert store.read_process(TASK_ID) is None
    finally:
        stop_process_group(task)


def test_cancel_kills_nested_command_session(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "nested-child.pid"
    nested_code = (
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    )
    task_code = (
        "import sys; from pilot.utils import run_command; "
        "run_command([sys.executable,'-c',sys.argv[1],sys.argv[2]])"
    )
    argv = [sys.executable, "-c", task_code, nested_code, str(child_pid_path)]
    store = create_running_task(tmp_path)
    task = start_owned_process(store, argv)

    try:
        deadline = time.monotonic() + 5
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(child_pid_path.read_text())

        TaskProcess(tmp_path).cancel(TASK_ID, grace_seconds=0.1)
        task.wait(timeout=5)

        deadline = time.monotonic() + 5
        while pid_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not pid_is_running(child_pid)
        assert store.read_status(TASK_ID) == TaskStatus.KILLED
    finally:
        stop_process_group(task)


def test_cancel_does_not_signal_a_stale_process_identity(tmp_path: Path) -> None:
    store = create_running_task(tmp_path)
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    process = start_owned_process(store, argv)
    record = TaskProcessRecord.from_dict(store.read_process(TASK_ID))
    stale = TaskProcessRecord(
        TASK_ID,
        argv,
        replace(record.identity, start_ticks=-1),
    )
    store.write_process(TASK_ID, process.pid, stale.to_dict())

    try:
        TaskRunner(tmp_path).kill(TASK_ID)

        assert process.poll() is None
        assert store.read_status(TASK_ID) == TaskStatus.FAILED
        assert store.read_metadata(TASK_ID)["failure"] == {
            "code": "task_interrupted"
        }
        assert store.read_process(TASK_ID) is None
    finally:
        stop_process_group(process)


def test_cancel_fails_closed_for_malformed_process_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_running_task(tmp_path)
    task_dir = store.task_dir(TASK_ID)
    (task_dir / "process.json").write_text("{}")
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *args: pytest.fail("an unverified process group was signalled"),
    )

    with pytest.raises(TaskNotRunningError, match="ownership is unavailable"):
        TaskRunner(tmp_path).kill(TASK_ID)

    assert store.read_status(TASK_ID) == TaskStatus.RUNNING
    assert (task_dir / "process.json").exists()
