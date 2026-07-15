from __future__ import annotations

import json
import os
import pickle
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import admin.backend.tasks.manager.task_runner as task_runner_module
from admin.backend.tasks.manager.task_runner import TaskRunner
from pilot.exceptions import TaskNotFoundError, TaskNotRunningError


TASK_ID = "20260715-120000-aabbcc"


def successful_callback(meta: dict) -> None:
    return None


def test_run_persists_task_before_starting_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    process = SimpleNamespace(pid=4321)

    def start_process(argv: list[str], **kwargs):
        assert (task_dir / "meta.json").exists()
        assert (task_dir / "status").read_text() == "running"
        assert (task_dir / "on_success.bin").exists()
        assert not (task_dir / "pid").exists()
        assert argv == [
            sys.executable,
            "-m",
            "admin.backend.tasks.manager.wrapper",
            str(task_dir),
        ]
        assert kwargs == {
            "start_new_session": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        return process

    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: TASK_ID))
    monkeypatch.setattr(task_runner_module.subprocess, "Popen", start_process)

    task_id = TaskRunner(tmp_path).run(
        "build",
        {},
        callbacks={"on_success": successful_callback, "on_failure": None},
    )

    meta = json.loads((task_dir / "meta.json").read_text())
    assert task_id == TASK_ID
    assert meta["task_id"] == TASK_ID
    assert meta["command"] == "build"
    assert meta["args"] == {}
    assert meta["bench_root"] == str(tmp_path)
    assert meta["finished_at"] is None
    assert meta["exit_code"] is None
    assert (task_dir / "pid").read_text() == "4321"
    assert pickle.loads((task_dir / "on_success.bin").read_bytes()) is successful_callback


def test_kill_signals_task_pid_and_marks_it_killed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "status").write_text("running")
    (task_dir / "pid").write_text("4321")
    signals = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))

    TaskRunner(tmp_path).kill(TASK_ID)

    assert signals == [(4321, signal.SIGTERM)]
    assert (task_dir / "status").read_text() == "killed"


def test_kill_marks_task_killed_when_pid_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "status").write_text("running")
    (task_dir / "pid").write_text("4321")

    def missing_process(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", missing_process)

    TaskRunner(tmp_path).kill(TASK_ID)

    assert (task_dir / "status").read_text() == "killed"


def test_kill_rejects_missing_task(tmp_path: Path) -> None:
    with pytest.raises(TaskNotFoundError, match=TASK_ID):
        TaskRunner(tmp_path).kill(TASK_ID)


def test_kill_rejects_completed_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "status").write_text("success")

    with pytest.raises(TaskNotRunningError, match="status=success"):
        TaskRunner(tmp_path).kill(TASK_ID)
