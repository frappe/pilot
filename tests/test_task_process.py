from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import pilot.tasks.manager.task_process as task_process_module
from pilot.tasks.manager.process_identity import ProcessIdentity
from pilot.tasks.manager.task_process import (
    TaskProcess,
    TaskProcessRecord,
    TaskProcessStartError,
)
from pilot.tasks.manager.task_state import TaskStatus
from pilot.tasks.manager.task_store import TaskStore

TASK_ID = "20260715-120000-aabbcc"


def create_running_task(
    bench_root: Path,
    command_argv: list[str] | None = None,
) -> TaskStore:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": TASK_ID,
            "command": "build",
            "args": {},
            "command_argv": command_argv or [sys.executable, "-c", "pass"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        },
        {"secrets.json": '{"admin_password":"secret"}'},
    )
    assert store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    return store


def identity(pid: int, launch_id: str) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        pgid=pid,
        sid=pid,
        boot_id="boot",
        start_ticks=1,
        uid=os.getuid(),
        argv_hash="argv",
        launch_id=launch_id,
    )


def test_start_persists_identity_before_releasing_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_running_task(tmp_path)
    manager = TaskProcess(tmp_path)
    captured = {}

    class Process:
        pid = 4321

    def start_process(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["reader"] = os.dup(kwargs["pass_fds"][0])
        return Process()

    monkeypatch.setattr(task_process_module.subprocess, "Popen", start_process)

    def capture(pid: int, argv: list[str], launch_id: str) -> ProcessIdentity:
        captured["launch_id"] = launch_id
        return identity(pid, launch_id)

    monkeypatch.setattr(manager._inspector, "capture", capture)
    original_write = os.write

    def release_gate(descriptor: int, value: bytes) -> int:
        assert store.read_pid(TASK_ID) == 4321
        assert store.read_process(TASK_ID) is not None
        captured["released"] = value
        return original_write(descriptor, value)

    monkeypatch.setattr(task_process_module.os, "write", release_gate)

    process = manager.start(TASK_ID)
    os.close(captured["reader"])

    record = manager.read(TASK_ID)
    assert process.pid == 4321
    assert isinstance(record, TaskProcessRecord)
    assert record.task_id == TASK_ID
    assert record.argv == captured["argv"]
    assert record.identity.launch_id == captured["launch_id"]
    assert captured["released"] == b"1"
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["env"]["BENCH_TASK_SECRETS_FILE"].endswith("secrets.json")
    assert captured["kwargs"]["env"]["BENCH_TASK_LAUNCH_ID"] == captured["launch_id"]
    assert captured["kwargs"]["env"]["PILOT_NONINTERACTIVE_PRIVILEGES"] == "1"


def test_identity_persistence_failure_aborts_before_task_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_running_task(tmp_path)
    manager = TaskProcess(tmp_path)
    captured = {}

    class Process:
        pid = 4321
        killed = False
        waited = False

        def kill(self) -> None:
            self.killed = True

        def wait(self) -> int:
            self.waited = True
            return 0

    process = Process()

    def start_process(argv, **kwargs):
        captured["reader"] = os.dup(kwargs["pass_fds"][0])
        return process

    monkeypatch.setattr(task_process_module.subprocess, "Popen", start_process)
    monkeypatch.setattr(
        manager._inspector,
        "capture",
        lambda pid, argv, launch_id: identity(pid, launch_id),
    )
    monkeypatch.setattr(
        manager._store,
        "write_process",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(TaskProcessStartError, match=TASK_ID):
        manager.start(TASK_ID)
    os.close(captured["reader"])

    assert process.killed is True
    assert process.waited is True
    assert store.read_status(TASK_ID) == TaskStatus.FAILED
    assert store.read_metadata(TASK_ID)["failure"] == {"code": "task_interrupted"}


def test_real_wrapper_waits_for_durable_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "side-effect.marker"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('done')",
    ]
    store = create_running_task(tmp_path, command)
    manager = TaskProcess(tmp_path)
    original_write = os.write

    def release_gate(descriptor: int, value: bytes) -> int:
        time.sleep(0.1)
        assert not marker.exists()
        assert store.read_process(TASK_ID) is not None
        assert store.read_pid(TASK_ID) is not None
        return original_write(descriptor, value)

    monkeypatch.setattr(task_process_module.os, "write", release_gate)

    process = manager.start(TASK_ID)
    process.wait(timeout=5)

    assert marker.read_text() == "done"
    assert store.read_status(TASK_ID) == TaskStatus.SUCCESS


def test_gate_eof_exits_wrapper_without_side_effect(tmp_path: Path) -> None:
    marker = tmp_path / "side-effect.marker"
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('done')",
    ]
    store = create_running_task(tmp_path, command)
    task_dir = store.task_dir(TASK_ID)
    read_fd, write_fd = os.pipe()
    argv = [
        sys.executable,
        "-m",
        "pilot.tasks.manager.wrapper",
        str(task_dir),
    ]
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env={
            **os.environ,
            "BENCH_TASK_READY_FD": str(read_fd),
            "BENCH_TASK_LAUNCH_ID": "gate-eof",
        },
        pass_fds=(read_fd,),
    )
    os.close(read_fd)
    os.close(write_fd)
    process.wait(timeout=5)

    assert not marker.exists()
    assert store.read_status(TASK_ID) == TaskStatus.RUNNING
