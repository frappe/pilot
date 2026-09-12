from __future__ import annotations

import contextlib
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, BenchNotRunningError, CommandError
from pilot.internal.atomic_file import atomic_write_private_text, exclusive_file_lock
from pilot.internal.tasks.process_identity import get_process_stamp
from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder
from pilot.utils import cli_root, run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def _tcp_port_open(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _pids_listening(port: int) -> set[int]:
    """PIDs listening on port (this user)."""
    from pilot.managers.platform import is_macos

    if is_macos():
        argv = ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"]
        pid_pattern = r"(\d+)"
    else:
        argv = ["ss", "-H", "-ltnp", f"sport = :{port}"]
        pid_pattern = r"pid=(\d+)"
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()
    return {int(m) for m in re.findall(pid_pattern, result.stdout)}


def _process_has_bench_root(pid: int, bench_root: Path) -> bool:
    from pilot.managers.platform import is_macos

    expected = f"{BENCH_ROOT_ENV}={bench_root}"
    if not is_macos():
        try:
            environment = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
        except OSError:
            return False
        return expected.encode() in environment
    try:
        result = subprocess.run(
            ["ps", "-E", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and re.search(rf"(?:^|\s){re.escape(expected)}(?:\s|$)", result.stdout) is not None
    )


_RELOAD_REQUEST_FILE = "reload.request"
_STOP_WAIT_SECONDS = 15.0
_STOP_POLL_SECONDS = 0.2
_SUPERVISOR_STOP_SECONDS = 30.0
BENCH_ROOT_ENV = "PILOT_BENCH_ROOT"
# Redis holds the job queue, and the admin plane is what issues the reload.
# Both must survive it, so only app-code processes are restarted.
_NON_RELOADABLE = frozenset({"admin", "admin-ui", "redis_cache", "redis_queue", "watch"})

# Stopped last, so their clients are already gone and cannot log a lost connection.
_DATASTORES = frozenset({"redis_cache", "redis_queue"})
_STOP_GRACE_SECONDS = 5

_COLORS = [
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[35m",
    "\033[34m",
    "\033[96m",
    "\033[92m",
    "\033[93m",
]
_RESET = "\033[0m"


class ProcessManager:
    def __init__(self, bench: "Bench", watch_admin_js: bool | None = None) -> None:
        self.bench = bench
        self.watch_admin_js = bench.config.watch_admin_js if watch_admin_js is None else watch_admin_js
        self._procs: dict[str, subprocess.Popen] = {}
        self._colors: dict[str, str] = {}
        self._stopping = False

    @classmethod
    def for_bench(cls, bench: "Bench") -> "ProcessManager":
        prod = bench.config.production
        if not prod.enabled:
            return ProcessManager(bench)
        if prod.process_manager == "systemd":
            from pilot.managers.processes.systemd import SystemdProcessManager

            return SystemdProcessManager(bench)
        from pilot.managers.processes.supervisor import SupervisorProcessManager

        return SupervisorProcessManager(bench)

    @classmethod
    def detect_running(cls, bench: "Bench") -> "ProcessManager":
        # Probe runtime state, not config presence, so a lingering config from a
        # switched manager can't mislead. Falls back to for_bench when none runs.
        from pilot.managers.processes.supervisor import SupervisorProcessManager
        from pilot.managers.processes.systemd import SystemdProcessManager

        for manager in (SystemdProcessManager(bench), SupervisorProcessManager(bench)):
            if manager.is_running():
                return manager
        return cls.for_bench(bench)

    @property
    def procfile_path(self) -> Path:
        return self.bench.config_path / "Procfile"

    @property
    def pid_file(self) -> Path:
        return self.bench.pids_path / "bench.pid"

    @property
    def reload_request_file(self) -> Path:
        return self.bench.pids_path / _RELOAD_REQUEST_FILE

    @property
    def python(self) -> Path:
        return self.bench.env_path / "bin" / "python"

    @property
    def _definitions(self) -> ProcessDefinitionBuilder:
        return ProcessDefinitionBuilder(self.bench, self.python, self.watch_admin_js)

    def write_config(self) -> None:
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        lines = [f"{pd.name}: {shlex.join(pd.argv)}\n" for pd in self._process_definitions()]
        self.procfile_path.write_text("".join(lines))

    def _ensure_gunicorn_config(self) -> None:
        GunicornManager(self.bench).generate_config()

    def _ensure_redis_config(self) -> None:
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()

    def is_configured(self) -> bool:
        return self.procfile_path.exists()

    def start(self) -> None:
        if not self.is_configured():
            raise BenchError(f"Procfile not found at {self.procfile_path}. Run 'pilot init' first.")
        self.write_config()
        pid = os.getpid()
        stamp = get_process_stamp(pid)
        if not stamp:
            raise BenchError("Could not capture the development supervisor identity.")
        self._write_supervisor_record(pid, stamp)
        try:
            self._run_processes(self._process_definitions())
        finally:
            self._unlink_supervisor_record(pid, stamp)
            self._cleanup_proc_pid_files()

    def start_workload(self) -> None:
        self.start()

    def stop(self) -> None:
        supervisor_stopped = self._stop_supervisor()
        processes_stopped = self._stop_port_holders()
        if not supervisor_stopped and not processes_stopped:
            raise BenchNotRunningError("Bench is not running.")
        self._wait_for_ports()

    def _stop_supervisor(self) -> bool:
        """SIGTERM the dev supervisor and wait for it to finish cleanup."""
        record = self._read_supervisor_record()
        if record is None:
            return False
        pid, recorded_stamp = record
        current_stamp = get_process_stamp(pid)
        if current_stamp is None:
            raise BenchError(f"Could not inspect bench supervisor {pid}. Try again.")
        if current_stamp != recorded_stamp:
            self._unlink_supervisor_record(pid, recorded_stamp)
            return False
        return self._terminate_supervisor(pid, recorded_stamp)

    def _terminate_supervisor(self, pid: int, stamp: str) -> bool:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._unlink_supervisor_record(pid, stamp)
            return False
        except PermissionError as exc:
            raise BenchError(f"Could not stop bench supervisor {pid}: permission denied.") from exc
        self._wait_for_exit(pid, stamp)
        self._unlink_supervisor_record(pid, stamp)
        return True

    def _stop_port_holders(self) -> bool:
        pids = {pid for port_pids in self._port_holders().values() for pid in port_pids}
        pids.discard(os.getpid())
        owned_pids = {pid for pid in pids if self._owns_process(pid)}
        for pid in owned_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                raise BenchError(f"Could not stop bench process {pid}: permission denied.") from exc
        return bool(owned_pids)

    def _owns_process(self, pid: int) -> bool:
        return _process_has_bench_root(pid, self.bench.path)

    def _port_holders(self) -> dict[int, set[int]]:
        return {port: pids for port in self._configured_ports if (pids := _pids_listening(port))}

    @property
    def _configured_ports(self) -> tuple[int, ...]:
        config = self.bench.config
        ports = [config.admin.port, config.http_port, config.redis.cache_port, config.redis.queue_port]
        if not self.bench.is_lite_mode:
            ports.append(config.socketio_port)
        return tuple(ports)

    def _wait_for_exit(self, pid: int, stamp: str, timeout: float = _SUPERVISOR_STOP_SECONDS) -> None:
        """Wait until the stamped process is gone; zombies and reused pids count
        as exited, inspection failures do not."""
        deadline = time.monotonic() + timeout
        while (current := get_process_stamp(pid)) == stamp or current is None:
            if time.monotonic() >= deadline:
                raise BenchError(f"Timed out waiting for bench supervisor {pid} to stop.")
            time.sleep(_STOP_POLL_SECONDS)

    def _wait_for_ports(self, timeout: float = _STOP_WAIT_SECONDS) -> None:
        """Wait until this bench's own processes release its ports; foreign holders are ignored."""
        deadline = time.monotonic() + timeout
        while held := {
            port
            for port, pids in self._port_holders().items()
            if any(self._owns_process(pid) for pid in pids)
        }:
            if time.monotonic() >= deadline:
                rendered = ", ".join(str(port) for port in sorted(held))
                raise BenchError(f"Timed out waiting for bench port(s) to be released: {rendered}.")
            time.sleep(_STOP_POLL_SECONDS)

    def _write_supervisor_record(self, pid: int, stamp: str) -> None:
        atomic_write_private_text(self.pid_file, f"{pid}\n{stamp}\n")

    def _read_supervisor_record(self) -> tuple[int, str] | None:
        """(pid, stamp); anything else in the file is not a record."""
        try:
            lines = self.pid_file.read_text().strip().splitlines()
            pid = int(lines[0])
            stamp = lines[1].strip()
        except (OSError, ValueError, IndexError):
            return None
        return (pid, stamp) if stamp else None

    def _unlink_supervisor_record(self, pid: int, stamp: str) -> None:
        """Remove the record only while it still holds the values this stop acted on."""
        with exclusive_file_lock(self.pid_file):
            if self._read_supervisor_record() == (pid, stamp):
                self.pid_file.unlink(missing_ok=True)

    @property
    def running_supervisor_pid(self) -> int | None:
        record = self._read_supervisor_record()
        if record is None:
            return None
        pid, recorded_stamp = record
        return pid if get_process_stamp(pid) == recorded_stamp else None

    def is_running(self) -> bool:
        return self.running_supervisor_pid is not None

    def stop_admin(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def restart_admin(self) -> None:
        pass

    def is_admin_running(self) -> bool:
        return _tcp_port_open(self.bench.config.admin.port)

    def reload_workers(self, web_only: bool = False) -> None:
        """Ask the running dev supervisor to restart its workload processes.

        Callers are separate processes (tasks, CLI), so this leaves a request
        the supervisor picks up rather than signalling processes it does not own."""
        self._clear_frappe_cache()
        if not self.is_running():
            return
        self.bench.pids_path.mkdir(parents=True, exist_ok=True)
        self.reload_request_file.write_text("web" if web_only else "workload")

    def _clear_frappe_cache(self) -> None:
        """Drop the cached app/module map and asset manifest, so restarted
        processes read apps.txt instead of importing a removed app."""
        if not self.bench.sites():
            return
        with contextlib.suppress(BenchError, CommandError, OSError):
            run_command(
                [*self.bench.frappe_call, "frappe", "--site", "all", "clear-cache"],
                cwd=self.bench.sites_path,
                timeout=120,
            )

    def _apply_reload_request(self, defs_by_name: dict[str, ProcessDefinition]) -> None:
        """Restart the processes a queued reload asked for, leaving admin alone."""
        try:
            scope = self.reload_request_file.read_text().strip()
        except OSError:
            return
        self.reload_request_file.unlink(missing_ok=True)
        names = ["web"] if scope == "web" else [n for n in self._procs if n not in _NON_RELOADABLE]
        for name in names:
            definition = defs_by_name.get(name)
            if definition is None or name not in self._procs:
                continue
            print(f"[{name}] reloading", file=sys.stderr)
            self._terminate(self._procs[name])
            self._spawn(definition)

    def _terminate(self, proc: subprocess.Popen) -> None:
        self._signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._signal_group(proc, signal.SIGKILL)

    def _signal_group(self, proc: subprocess.Popen, signum: int) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(proc.pid), signum)

    def _spawn(self, pd: ProcessDefinition) -> None:
        proc = subprocess.Popen(
            pd.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            cwd=str(pd.working_dir) if pd.working_dir else None,
            env={**os.environ, **pd.env, BENCH_ROOT_ENV: str(self.bench.path)},
        )
        self._procs[pd.name] = proc
        (self.bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))
        threading.Thread(target=self._stream, args=(pd.name, proc, self._color(pd.name)), daemon=True).start()

    def _color(self, name: str) -> str:
        return self._colors.setdefault(name, _COLORS[len(self._colors) % len(_COLORS)])

    def _run_processes(self, defs: list[ProcessDefinition]) -> None:
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(_signum, _frame):
            if self._stopping:
                return
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        try:
            self.reload_request_file.unlink(missing_ok=True)
            for pd in defs:
                self._spawn(pd)
            self._supervise(defs)
        finally:
            self._stop_all()
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)

    def _supervise(self, defs: list[ProcessDefinition]) -> None:
        defs_by_name = {pd.name: pd for pd in defs}
        is_critical = {pd.name: pd.critical for pd in defs}
        while not self._stopping:
            for name, proc in list(self._procs.items()):
                if proc.poll() is None:
                    continue
                if is_critical[name]:
                    print(f"[{name}] exited with code {proc.returncode}", file=sys.stderr)
                    self._stopping = True
                    break
                print(f"[{name}] exited with code {proc.returncode}; continuing without it", file=sys.stderr)
                del self._procs[name]
                (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)
            if not self._stopping:
                self._apply_reload_request(defs_by_name)
                time.sleep(0.5)

    def _stream(self, name: str, proc: subprocess.Popen, color: str) -> None:
        assert proc.stdout is not None
        prefix = f"{color}[{name}]{_RESET} "
        for raw in proc.stdout:
            sys.stdout.write(prefix + raw.decode(errors="replace") + _RESET)
            sys.stdout.flush()

    def _stop_all(self) -> None:
        """Drain the workload before redis. Killing them together leaves the workers
        and the realtime bridge retrying a socket that is already closed."""
        names = list(self._procs)
        self._stop_group([name for name in names if name not in _DATASTORES])
        self._stop_group([name for name in names if name in _DATASTORES])
        self.reload_request_file.unlink(missing_ok=True)

    def _stop_group(self, names: list[str]) -> None:
        """SIGTERM the whole group first, then share one deadline, so slow
        children drain concurrently and get reaped after a kill."""
        procs = [self._procs[name] for name in names if name in self._procs]
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        for proc in procs:
            self._signal_group(proc, signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=_STOP_GRACE_SECONDS)

    def _cleanup_proc_pid_files(self) -> None:
        for name in self._procs:
            (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)

    def _prod_process_definitions(self) -> list[ProcessDefinition]:
        return self._definitions.prod_process_definitions()

    def _process_definitions(self) -> list[ProcessDefinition]:
        return self._definitions.process_definitions()
