"""The dev runner must actually honor restart_on_failure - it previously only
rendered that field into systemd/supervisor config and never read it itself,
so a non-critical process that crashed was just dropped forever, and a
critical one (the default) took the whole bench down regardless of the flag."""

from __future__ import annotations

import time
from pathlib import Path

from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.local import ProcessManager
from tests.pilot.commands.test_commands import make_bench


def _manager(tmp_path: Path) -> ProcessManager:
    bench = make_bench(tmp_path)
    bench.create_directories()
    return ProcessManager(bench)


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition never became true")


def test_non_critical_restart_on_failure_respawns_after_a_bad_exit(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    pd = ProcessDefinition(
        name="app-x",
        argv=["sh", "-c", "exit 1"],
        log_file=manager.bench.logs_path / "app-x.log",
        critical=False,
        restart_on_failure=True,
    )
    manager._spawn(pd)
    first_pid = manager._procs["app-x"].pid

    _wait_until(lambda: manager._procs["app-x"].poll() is not None)
    manager._reap_exited({"app-x": pd})

    assert not manager._stopping
    assert "app-x" in manager._procs  # respawned, not dropped
    assert manager._procs["app-x"].pid != first_pid


def test_non_critical_without_restart_on_failure_is_dropped(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    pd = ProcessDefinition(
        name="app-x",
        argv=["sh", "-c", "exit 1"],
        log_file=manager.bench.logs_path / "app-x.log",
        critical=False,
        restart_on_failure=False,
    )
    manager._spawn(pd)
    _wait_until(lambda: manager._procs["app-x"].poll() is not None)
    manager._reap_exited({"app-x": pd})

    assert not manager._stopping
    assert "app-x" not in manager._procs  # dropped, no restart


def test_critical_process_exiting_stops_the_bench_even_with_restart_on_failure(tmp_path: Path) -> None:
    """critical takes priority: a core bench process must never be silently
    respawned in place of the bench actually noticing it's down."""
    manager = _manager(tmp_path)
    pd = ProcessDefinition(
        name="web",
        argv=["sh", "-c", "exit 1"],
        log_file=manager.bench.logs_path / "web.log",
        critical=True,
        restart_on_failure=True,
    )
    manager._spawn(pd)
    _wait_until(lambda: manager._procs["web"].poll() is not None)
    manager._reap_exited({"web": pd})

    assert manager._stopping


def test_non_critical_restart_on_failure_does_not_restart_a_clean_exit(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    pd = ProcessDefinition(
        name="app-x",
        argv=["sh", "-c", "exit 0"],
        log_file=manager.bench.logs_path / "app-x.log",
        critical=False,
        restart_on_failure=True,
    )
    manager._spawn(pd)
    _wait_until(lambda: manager._procs["app-x"].poll() is not None)
    manager._reap_exited({"app-x": pd})

    assert "app-x" not in manager._procs  # exit 0 is not a failure - not restarted
