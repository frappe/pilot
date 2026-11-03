from __future__ import annotations

import os
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.bench.job_queue import _PROGRAM, JobQueue, JobQueueState
from pilot.exceptions import BenchError


def _queue(tmp_path: Path) -> JobQueue:
    bench = MagicMock()
    bench.path = tmp_path
    bench.python = tmp_path / "env" / "bin" / "python"
    bench.config.redis.queue_port = 11000
    return JobQueue(bench)


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def test_suspend_runs_on_the_bench_python_against_its_queue(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    output = 'rq says hello\n{"is_suspended": true, "active_workers": ["worker-1"]}\n'

    with patch("pilot.core.bench.job_queue.run_command", return_value=_completed(output)) as run:
        state = queue.suspend()

    argv = run.call_args.args[0]
    assert argv[:4] == [str(tmp_path / "env" / "bin" / "python"), "-I", "-c", _PROGRAM]
    assert argv[-2:] == ["suspend", "redis://localhost:11000"]
    assert state == JobQueueState(is_suspended=True, active_workers=["worker-1"])
    assert not state.is_held


_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _get_active_workers(workers: list) -> list[str]:
    """The program's own function. Its `main` runs only as `__main__`."""
    namespace: dict = {"__name__": "job_queue_program"}
    exec(compile(_PROGRAM, "job_queue_program", "exec"), namespace)
    return namespace["get_active_workers"](workers)


def _worker(
    name: str, state: str, *, pid: int | None = None, host: str | None = None, **times
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        hostname=host or socket.gethostname(),
        pid=os.getpid() if pid is None else pid,
        get_state=lambda: state,
        birth_date=times.get("born"),
        last_heartbeat=times.get("beat"),
    )


def test_only_live_workers_that_are_not_suspended_take_jobs() -> None:
    workers = [
        _worker("suspended", "suspended"),
        _worker("idle", "idle"),
        _worker("dead", "busy", pid=2**22 + 1),
        _worker("remote", "idle", pid=2**22 + 1, host="another-host"),
    ]

    assert _get_active_workers(workers) == ["idle", "remote"]


def test_a_busy_lite_thread_counts_even_when_a_younger_sibling_is_suspended() -> None:
    """A no-fork worker sends no heartbeat during a job, so its heartbeat is old while it works."""
    busy = _worker("busy", "busy", beat=_NOW)
    sibling = _worker("sibling", "suspended", born=_NOW + timedelta(minutes=5))

    assert _get_active_workers([busy, sibling]) == ["busy"]


def test_a_suspended_queue_without_active_workers_is_held() -> None:
    assert JobQueueState(is_suspended=True, active_workers=[]).is_held
    assert not JobQueueState(is_suspended=False, active_workers=[]).is_held


def test_unreadable_output_fails_loudly(tmp_path: Path) -> None:
    with (
        patch("pilot.core.bench.job_queue.run_command", return_value=_completed("")),
        pytest.raises(BenchError, match="Cannot read the job queue state"),
    ):
        _ = _queue(tmp_path).state
