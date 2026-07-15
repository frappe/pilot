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
import admin.backend.tasks.manager.wrapper as wrapper_module
from admin.backend.tasks.manager.task_reader import TaskReader
from admin.backend.tasks.manager.task_runner import TaskRunner
from admin.backend.tasks.manager.wrapper import callback_handler, run_with_syslog_output
from pilot.exceptions import TaskNotFoundError, TaskNotRunningError


TASK_ID = "20260715-120000-aabbcc"


def successful_callback(meta: dict) -> None:
    return None


def write_success_marker(meta: dict) -> None:
    (Path(meta["bench_root"]) / "success.marker").write_text("")


def write_failure_marker(meta: dict) -> None:
    (Path(meta["bench_root"]) / "failure.marker").write_text("")


def failing_callback(meta: dict) -> None:
    raise RuntimeError("callback error")


def task_meta(bench_root: Path) -> dict:
    return {
        "task_id": TASK_ID,
        "command": "build",
        "args": {},
        "command_argv": [sys.executable, "-c", "print('done')"],
        "started_at": "2026-07-15T12:00:00+00:00",
        "finished_at": None,
        "exit_code": None,
        "bench_root": str(bench_root),
    }


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
    assert set(meta) == {
        "args",
        "bench_root",
        "command",
        "command_argv",
        "exit_code",
        "finished_at",
        "started_at",
        "task_id",
    }
    assert meta["task_id"] == TASK_ID
    assert meta["command"] == "build"
    assert meta["args"] == {}
    assert meta["bench_root"] == str(tmp_path)
    assert meta["finished_at"] is None
    assert meta["exit_code"] is None
    assert (task_dir / "pid").read_text() == "4321"
    assert pickle.loads((task_dir / "on_success.bin").read_bytes()) is successful_callback


@pytest.mark.parametrize("status", ["running", "success", "failed", "killed"])
def test_task_reader_preserves_current_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text(status)
    (task_dir / "pid").write_text("4321")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    task = TaskReader(tmp_path).read_task(TASK_ID)

    assert task.status == status
    assert task.pid == 4321


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


def test_wrapper_output_is_readable_without_syslog_envelopes(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    output_path = task_dir / "output.log"
    command = [
        sys.executable,
        "-c",
        "import sys; print('standard output', flush=True); print('standard error', file=sys.stderr, flush=True)",
    ]

    exit_code = run_with_syslog_output(command, str(tmp_path), "build", output_path)

    meta = task_meta(tmp_path)
    meta["exit_code"] = exit_code
    meta["finished_at"] = "2026-07-15T12:00:01+00:00"
    (task_dir / "meta.json").write_text(json.dumps(meta))
    (task_dir / "status").write_text("success")
    assert exit_code == 0
    assert TaskReader(tmp_path).read_output(TASK_ID) == ["standard output", "standard error"]


@pytest.mark.parametrize(
    ("exit_code", "status", "marker", "other_marker"),
    [
        (0, "success", "success.marker", "failure.marker"),
        (1, "failed", "failure.marker", "success.marker"),
    ],
)
def test_wrapper_runs_matching_callback_and_finalizes_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    status: str,
    marker: str,
    other_marker: str,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "on_success.bin").write_bytes(pickle.dumps(write_success_marker))
    (task_dir / "on_failure.bin").write_bytes(pickle.dumps(write_failure_marker))
    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", lambda *args: exit_code)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    final_meta = json.loads((task_dir / "meta.json").read_text())
    assert (tmp_path / marker).exists()
    assert not (tmp_path / other_marker).exists()
    assert not (task_dir / "on_success.bin").exists()
    assert not (task_dir / "on_failure.bin").exists()
    assert (task_dir / "status").read_text() == status
    assert final_meta["exit_code"] == exit_code
    assert final_meta["finished_at"] is not None
    assert "Callback successfully triggered" in (task_dir / "output.log").read_text()


def test_callback_failure_is_logged_and_callback_is_removed(tmp_path: Path) -> None:
    callback_path = tmp_path / "on_failure.bin"
    output_path = tmp_path / "output.log"
    callback_path.write_bytes(pickle.dumps(failing_callback))

    callback_handler(callback_path, output_path, task_meta(tmp_path))

    assert not callback_path.exists()
    assert "Callback failed: callback error" in output_path.read_text()
