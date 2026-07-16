from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pilot.tasks.manager.task_runner import TaskRunner
from pilot.tasks.manager.worker import TaskWorker
from pilot.managers.process_manager import ProcessDefinition
from pilot.managers.process_managers.supervisor import SupervisorRenderer
from pilot.managers.process_managers.systemd import SystemdRenderer


def wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            value = path.read_text().strip()
            if value:
                return int(value)
        time.sleep(0.01)
    raise AssertionError("task process did not start")


def pid_is_running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return state != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def stop_process_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass


@pytest.mark.parametrize("mode", ["dev", "systemd", "supervisor"])
def test_task_survives_admin_shutdown(tmp_path: Path, mode: str) -> None:
    task_pid_path = tmp_path / f"{mode}.pid"
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "start_new_session=True); Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    admin = subprocess.Popen(
        [sys.executable, "-c", parent_code, str(task_pid_path)],
        start_new_session=True,
    )
    task_pid = None

    try:
        task_pid = wait_for_pid(task_pid_path)
        if mode == "systemd":
            os.kill(admin.pid, signal.SIGTERM)
        else:
            os.killpg(os.getpgid(admin.pid), signal.SIGTERM)
        admin.wait(timeout=5)

        os.kill(task_pid, 0)
    finally:
        if admin.poll() is None:
            stop_process_group(admin.pid)
            admin.wait(timeout=5)
        if task_pid is not None:
            stop_process_group(task_pid)


def test_systemd_admin_shutdown_signals_only_the_admin_process(tmp_path: Path) -> None:
    process = ProcessDefinition(
        "admin",
        "/env/bin/gunicorn admin.backend.wsgi:application",
        tmp_path / "admin.log",
        working_dir=tmp_path,
    )

    service = SystemdRenderer("test-bench").admin_service(
        process,
        "test-bench-admin.socket",
    )

    assert "KillMode=process" in service
    assert "Restart=no" in service
    assert "PartOf=" not in service


def test_supervisor_admin_shutdown_signals_only_the_admin_group(tmp_path: Path) -> None:
    process = ProcessDefinition(
        "admin",
        "/env/bin/python -m admin.backend.server",
        tmp_path / "admin.log",
    )
    renderer = SupervisorRenderer("test-bench", tmp_path)

    program = renderer.render(process)
    config = renderer.conf([process], tmp_path / "supervisor.sock", tmp_path / "supervisor.pid")

    assert "stopasgroup=true" in program
    assert "killasgroup=true" in program
    assert "[group:test-bench-admin]" in config
    assert "programs=test-bench-admin" in config


def test_task_cancel_stops_wrapper_and_workload_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workload_pid_path = tmp_path / "workload.pid"
    workload = [
        sys.executable,
        "-c",
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
        str(workload_pid_path),
    ]
    monkeypatch.setattr(TaskRunner, "_build_argv", lambda self, command, args: workload)
    runner = TaskRunner(tmp_path)
    worker = TaskWorker(tmp_path)
    wrapper_pid = None

    try:
        task_id = runner.run("build", {})
        worker.start()
        wrapper_pid = wait_for_pid(tmp_path / "tasks" / task_id / "pid")
        workload_pid = wait_for_pid(workload_pid_path)
        assert os.getpgid(wrapper_pid) == wrapper_pid
        assert os.getpgid(workload_pid) == wrapper_pid

        runner.kill(task_id)
        worker.request_drain()
        worker.join(5)
        assert not worker.is_alive()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and pid_is_running(workload_pid):
            time.sleep(0.01)
        if pid_is_running(workload_pid):
            raise AssertionError("task workload survived cancellation")
    finally:
        if wrapper_pid is not None:
            try:
                os.killpg(wrapper_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        worker.request_drain()
        worker.join(5)
