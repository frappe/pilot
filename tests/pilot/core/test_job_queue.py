from __future__ import annotations

import subprocess
from pathlib import Path
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
    assert argv[:3] == [str(tmp_path / "env" / "bin" / "python"), "-I", "-c"]
    assert argv[-2:] == ["suspend", "redis://localhost:11000"]
    assert state == JobQueueState(is_suspended=True, active_workers=["worker-1"])
    assert not state.is_held


def test_the_program_is_valid_python() -> None:
    compile(_PROGRAM, "job_queue", "exec")


def test_a_suspended_queue_without_active_workers_is_held() -> None:
    assert JobQueueState(is_suspended=True, active_workers=[]).is_held
    assert not JobQueueState(is_suspended=False, active_workers=[]).is_held


def test_unreadable_output_fails_loudly(tmp_path: Path) -> None:
    with (
        patch("pilot.core.bench.job_queue.run_command", return_value=_completed("")),
        pytest.raises(BenchError, match="Cannot read the job queue state"),
    ):
        _ = _queue(tmp_path).state
