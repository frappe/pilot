from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pilot.managers.task.models import TaskStatus
from pilot.internal.tasks.store import TaskStore

TASK_ID = "20260715-120000-aabbcc"


def create_running_task(bench_root: Path) -> TaskStore:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": TASK_ID,
            "command": "build",
            "args": {},
            "command_argv": ["bench", "build"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        }
    )
    assert store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    return store


def wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and (value := path.read_text().strip()):
            return int(value)
        time.sleep(0.01)
    raise AssertionError("watchdog process did not start")


def pid_is_running(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def stop_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_watchdog_defers_for_tasks_and_signals_only_admin_owner(
    tmp_path: Path,
) -> None:
    store = create_running_task(tmp_path)
    worker_pid_path = tmp_path / "watchdog-worker.pid"
    worker_code = (
        "import sys,time; from pathlib import Path; "
        "from admin.backend.watchdog import AdminIdleWatchdog,AdminProcessOwner; "
        "Path(sys.argv[2]).write_text(str(__import__('os').getpid())); "
        "AdminIdleWatchdog(Path(sys.argv[1]),0.1,AdminProcessOwner.parent())._watch(); "
        "time.sleep(60)"
    )
    owner_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
        "time.sleep(60)"
    )
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            owner_code,
            worker_code,
            str(tmp_path),
            str(worker_pid_path),
        ],
        start_new_session=True,
    )
    workload = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    worker_pid = None

    try:
        worker_pid = wait_for_pid(worker_pid_path)
        time.sleep(0.35)
        assert owner.poll() is None
        assert pid_is_running(worker_pid)
        assert workload.poll() is None

        assert store.transition(TASK_ID, TaskStatus.RUNNING, TaskStatus.SUCCESS)
        owner.wait(timeout=5)

        assert owner.returncode == -signal.SIGTERM
        assert pid_is_running(worker_pid)
        assert workload.poll() is None
    finally:
        stop_group(owner.pid)
        stop_group(workload.pid)
        if owner.poll() is None:
            owner.wait(timeout=5)
        if workload.poll() is None:
            workload.wait(timeout=5)
