"""Tests for stopping the dev bench supervisor and its leftovers."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from pilot.exceptions import BenchError, BenchNotRunningError
from pilot.managers.processes import local as process_module
from pilot.managers.processes.local import ProcessManager
from tests.pilot.managers.test_managers_extra import make_bench


def _manager(tmp_path: Path) -> ProcessManager:
    bench = make_bench(tmp_path)
    bench.pids_path.mkdir(parents=True, exist_ok=True)
    return ProcessManager(bench)


def _spawn_reaped_sleep(
    *, start_new_session: bool = False, bench_root: Path | None = None
) -> subprocess.Popen:
    """A sleeping child whose zombie gets reaped, so its process stamp vanishes on exit.

    Spawns python, not /bin/sleep: macOS hides the environment of Apple platform
    binaries from `ps -E`, which would defeat the ownership check."""
    env = None
    if bench_root is not None:
        env = {**process_module.os.environ, process_module.BENCH_ROOT_ENV: str(bench_root)}
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=start_new_session,
        env=env,
    )
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def _record_supervisor(manager: ProcessManager, proc: subprocess.Popen) -> None:
    stamp = process_module.get_process_stamp(proc.pid)
    manager.pid_file.write_text(f"{proc.pid}\n{stamp}\n")


@pytest.mark.parametrize(
    ("macos", "stdout", "expected_argv", "expected_pids"),
    [
        (True, "123\n456\n", ["lsof", "-ti", "tcp:7001", "-sTCP:LISTEN"], {123, 456}),
        (
            False,
            'users:(("python",pid=123,fd=4))\nusers:(("redis",pid=456,fd=5))\n',
            ["ss", "-H", "-ltnp", "sport = :7001"],
            {123, 456},
        ),
    ],
)
def test_pids_listening_uses_platform_tool(
    monkeypatch, macos: bool, stdout: str, expected_argv: list[str], expected_pids: set[int]
) -> None:
    run = MagicMock(return_value=subprocess.CompletedProcess(expected_argv, 0, stdout=stdout))
    monkeypatch.setattr("pilot.managers.platform.is_macos", lambda: macos)
    monkeypatch.setattr(process_module.subprocess, "run", run)

    assert process_module._pids_listening(7001) == expected_pids
    assert run.call_args.args[0] == expected_argv


def test_read_supervisor_record_requires_pid_and_stamp(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    manager.pid_file.write_text("123\nstamp value\n")
    assert manager._read_supervisor_record() == (123, "stamp value")

    for content in ("123\n", "not a pid", ""):
        manager.pid_file.write_text(content)
        assert manager._read_supervisor_record() is None

    manager.pid_file.unlink()
    assert manager._read_supervisor_record() is None


def test_stop_terminates_supervisor_and_waits_for_ports(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    proc = _spawn_reaped_sleep()
    _record_supervisor(manager, proc)
    wait_for_ports = MagicMock()
    monkeypatch.setattr(manager, "_wait_for_ports", wait_for_ports)

    manager.stop()

    assert proc.poll() is not None
    assert not manager.pid_file.exists()
    wait_for_ports.assert_called_once_with()


def test_stop_also_terminates_orphans_after_supervisor(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    stop_supervisor = MagicMock(return_value=True)
    stop_port_holders = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_stop_supervisor", stop_supervisor)
    monkeypatch.setattr(manager, "_stop_port_holders", stop_port_holders)
    monkeypatch.setattr(manager, "_wait_for_ports", lambda: None)

    manager.stop()

    stop_supervisor.assert_called_once_with()
    stop_port_holders.assert_called_once_with()


def test_stop_preserves_record_replaced_during_shutdown(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("123\noriginal\n")

    def replace_record(_pid: int, _stamp: str, timeout: float | None = None) -> None:
        manager.pid_file.write_text("456\nreplacement\n")

    monkeypatch.setattr("pilot.managers.processes.local.os.kill", MagicMock())
    monkeypatch.setattr(process_module, "get_process_stamp", lambda _pid: "original")
    monkeypatch.setattr(manager, "_wait_for_exit", replace_record)
    monkeypatch.setattr(manager, "_wait_for_ports", lambda: None)

    manager.stop()

    assert manager._read_supervisor_record() == (456, "replacement")


def test_supervisor_cleanup_preserves_replacement_record(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "is_configured", lambda: True)
    monkeypatch.setattr(manager, "write_config", lambda: None)
    monkeypatch.setattr(manager, "_process_definitions", lambda: [])

    def replace_supervisor(_definitions) -> None:
        manager.pid_file.write_text("456\nreplacement\n")

    monkeypatch.setattr(manager, "_run_processes", replace_supervisor)

    manager.start()

    assert manager._read_supervisor_record() == (456, "replacement")


def test_stop_with_stale_pid_file_kills_port_holders(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("999999\nstale-stamp\n")
    orphan = _spawn_reaped_sleep(start_new_session=True, bench_root=manager.bench.path)
    monkeypatch.setattr(
        "pilot.managers.processes.local._pids_listening",
        lambda port: (
            {orphan.pid} if port == manager.bench.config.redis.queue_port and orphan.poll() is None else set()
        ),
    )

    manager.stop()

    orphan.wait(timeout=5)
    assert not manager.pid_file.exists()


def test_stop_ignores_stamp_less_pid_file(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("123")
    monkeypatch.setattr(manager, "_port_holders", lambda: {})
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "kill", kill)

    with pytest.raises(BenchNotRunningError, match="not running"):
        manager.stop()

    kill.assert_not_called()


def test_stop_raises_when_supervisor_inspection_fails(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("123\noriginal\n")
    monkeypatch.setattr(process_module, "get_process_stamp", lambda _pid: None)
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "kill", kill)

    with pytest.raises(BenchError, match="Could not inspect bench supervisor"):
        manager.stop()

    kill.assert_not_called()
    assert manager.pid_file.exists()


def test_stop_preserves_supervisor_record_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("123\noriginal\n")
    monkeypatch.setattr(process_module, "get_process_stamp", lambda _pid: "original")
    monkeypatch.setattr(process_module.os, "kill", MagicMock(side_effect=PermissionError))

    with pytest.raises(BenchError, match="permission denied"):
        manager.stop()

    assert manager._read_supervisor_record() == (123, "original")


def test_stop_ignores_unowned_port_holder(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_port_holders", lambda: {7000: {123}})
    monkeypatch.setattr(process_module, "_process_has_bench_root", lambda _pid, _root: False)
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "kill", kill)

    with pytest.raises(BenchNotRunningError, match="not running"):
        manager.stop()

    kill.assert_not_called()


def test_stop_signals_owned_and_skips_foreign_holder(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_port_holders", lambda: {7000: {123}, 8000: {456}})
    monkeypatch.setattr(manager, "_wait_for_ports", lambda: None)
    monkeypatch.setattr(process_module, "_process_has_bench_root", lambda pid, _root: pid == 456)
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "kill", kill)

    manager.stop()

    kill.assert_called_once_with(456, process_module.signal.SIGTERM)


def test_stop_does_not_signal_reused_supervisor_pid(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    manager.pid_file.write_text("123\noriginal\n")
    monkeypatch.setattr(process_module, "get_process_stamp", lambda _pid: "replacement")
    monkeypatch.setattr(manager, "_port_holders", lambda: {})
    kill = MagicMock()
    monkeypatch.setattr(process_module.os, "kill", kill)

    with pytest.raises(BenchNotRunningError, match="not running"):
        manager.stop()

    kill.assert_not_called()
    assert not manager.pid_file.exists()


def test_macos_bench_root_match_requires_environment_boundary(tmp_path: Path, monkeypatch) -> None:
    bench_root = tmp_path / "bench"
    output = f"python worker.py {process_module.BENCH_ROOT_ENV}={bench_root}2 OTHER=value"
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=output))
    monkeypatch.setattr("pilot.managers.platform.is_macos", lambda: True)
    monkeypatch.setattr(process_module.subprocess, "run", run)

    assert process_module._process_has_bench_root(123, bench_root) is False

    run.return_value = subprocess.CompletedProcess(
        [], 0, stdout=f"python worker.py {process_module.BENCH_ROOT_ENV}={bench_root} OTHER=value"
    )
    assert process_module._process_has_bench_root(123, bench_root) is True


def test_stop_raises_when_nothing_is_running(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr("pilot.managers.processes.local._pids_listening", lambda port: set())

    with pytest.raises(BenchNotRunningError, match="not running"):
        manager.stop()


def test_configured_ports_skips_socketio_in_lite_mode(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(type(manager.bench), "is_lite_mode", property(lambda _self: True))

    assert manager.bench.config.socketio_port not in manager._configured_ports


def test_wait_for_exit_raises_instead_of_silently_timing_out(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proc = _spawn_reaped_sleep()
    stamp = process_module.get_process_stamp(proc.pid)
    try:
        with pytest.raises(BenchError, match="Timed out waiting for bench supervisor"):
            manager._wait_for_exit(proc.pid, stamp, timeout=0)
    finally:
        proc.terminate()
        proc.wait()


def test_wait_for_exit_treats_unreaped_zombie_as_exited(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    proc = subprocess.Popen(["sleep", "30"])
    stamp = process_module.get_process_stamp(proc.pid)
    proc.terminate()
    try:
        manager._wait_for_exit(proc.pid, stamp, timeout=5)
    finally:
        proc.wait()


def test_wait_for_exit_keeps_waiting_while_inspection_fails(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(process_module, "get_process_stamp", lambda _pid: None)

    with pytest.raises(BenchError, match="Timed out waiting for bench supervisor"):
        manager._wait_for_exit(123, "stamp", timeout=0)


def test_wait_for_ports_reports_the_ports_still_in_use(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_port_holders", lambda: {11000: {123}, 13000: {456}})
    monkeypatch.setattr(manager, "_owns_process", lambda _pid: True)

    with pytest.raises(BenchError, match="11000, 13000"):
        manager._wait_for_ports(timeout=0)


def test_wait_for_ports_ignores_foreign_holders(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_port_holders", lambda: {11000: {123}})
    monkeypatch.setattr(manager, "_owns_process", lambda _pid: False)

    manager._wait_for_ports(timeout=0)


def test_wait_for_ports_rechecks_process_ownership(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    holders = iter(({11000: {123}}, {11000: {456}}))
    monkeypatch.setattr(manager, "_port_holders", lambda: next(holders))
    monkeypatch.setattr(manager, "_owns_process", lambda pid: pid == 123)
    monkeypatch.setattr(process_module.time, "sleep", lambda _seconds: None)

    manager._wait_for_ports(timeout=1)


def test_run_processes_cleans_up_and_restores_signals_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    original_term = object()
    original_int = object()
    set_signal = MagicMock()
    stop_all = MagicMock()
    monkeypatch.setattr(
        process_module.signal, "getsignal", MagicMock(side_effect=[original_term, original_int])
    )
    monkeypatch.setattr(process_module.signal, "signal", set_signal)
    monkeypatch.setattr(manager, "_spawn", MagicMock(side_effect=RuntimeError("spawn failed")))
    monkeypatch.setattr(manager, "_stop_all", stop_all)

    with pytest.raises(RuntimeError, match="spawn failed"):
        manager._run_processes([MagicMock()])

    stop_all.assert_called_once_with()
    assert set_signal.call_args_list[-2:] == [
        call(process_module.signal.SIGTERM, original_term),
        call(process_module.signal.SIGINT, original_int),
    ]


def test_stop_all_escalates_to_sigkill_after_deadline(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    proc = MagicMock()
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="web", timeout=1), None]
    manager._procs = {"web": proc}
    signals = []
    monkeypatch.setattr(manager, "_signal_group", lambda _proc, signum: signals.append(signum))

    manager._stop_all()

    assert signals == [process_module.signal.SIGTERM, process_module.signal.SIGKILL]
