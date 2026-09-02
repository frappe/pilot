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

from pilot.exceptions import BenchError, CommandError
from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.definitions import (
    ProcessDefinition,
    ProcessDefinitionBuilder,
    hook_wrapped_argv,
)
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
    """PIDs listening on port (this user), via ss."""
    try:
        result = subprocess.run(
            ["ss", "-H", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()
    return {int(m) for m in re.findall(r"pid=(\d+)", result.stdout)}


_RELOAD_REQUEST_FILE = "reload.request"
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
        lines = [f"{pd.name}: {shlex.join(hook_wrapped_argv(pd))}\n" for pd in self._process_definitions()]
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
        self.pid_file.write_text(str(os.getpid()))
        try:
            self._run_processes(self._process_definitions())
        finally:
            self.pid_file.unlink(missing_ok=True)
            self._cleanup_proc_pid_files()

    def start_workload(self) -> None:
        self.start()

    def stop(self) -> None:
        if self.pid_file.exists():
            pid = int(self.pid_file.read_text().strip())
            self.pid_file.unlink(missing_ok=True)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError as exc:
                raise BenchError(f"Process {pid} is not running. Removed stale PID file.") from exc
            return

        # No pid file (e.g. pre-init setup wizard): stop by port.
        config = self.bench.config
        pids = set()
        for port in (config.admin.port, config.http_port):
            pids |= _pids_listening(port)
        if not pids:
            raise BenchError("Bench is not running.")
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

    def is_running(self) -> bool:
        if not self.pid_file.exists():
            return False
        try:
            os.kill(int(self.pid_file.read_text().strip()), 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            return False

    def stop_admin(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def restart_admin(self) -> None:
        pass

    def is_admin_running(self) -> bool:
        return _tcp_port_open(self.bench.config.admin.port)

    def live_states(self) -> dict[str, dict]:
        """{process_name: {status, pid}}, read from pid files the running dev
        supervisor already writes per process. The dev runner has no separate
        control daemon to send a start/stop/restart to from another process,
        so this is status-only."""
        if not self.is_running() or not self.bench.pids_path.is_dir():
            return {}
        states: dict[str, dict] = {}
        for pid_file in sorted(self.bench.pids_path.glob("*.pid")):
            name = pid_file.stem
            if name == "bench":
                continue
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                status = "running"
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pid, status = None, "stopped"
            states[name] = {"status": status, "pid": pid}
        return states

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
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    def _spawn(self, pd: ProcessDefinition) -> None:
        proc = subprocess.Popen(
            hook_wrapped_argv(pd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            cwd=str(pd.working_dir) if pd.working_dir else None,
            env={**os.environ, **pd.env} if pd.env else None,
        )
        self._procs[pd.name] = proc
        (self.bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))
        threading.Thread(target=self._stream, args=(pd.name, proc, self._color(pd.name)), daemon=True).start()

    def _color(self, name: str) -> str:
        return self._colors.setdefault(name, _COLORS[len(self._colors) % len(_COLORS)])

    def _reap_exited(self, defs_by_name: dict[str, ProcessDefinition]) -> None:
        """One pass over running processes: restart a non-critical one that
        failed and is marked restart_on_failure, drop a non-critical one that
        exited otherwise, or stop the whole bench if a critical one exited."""
        for name, proc in list(self._procs.items()):
            if proc.poll() is None:
                continue
            pd = defs_by_name[name]
            if pd.critical:
                print(f"[{name}] exited with code {proc.returncode}", file=sys.stderr)
                self._stopping = True
                break
            if pd.restart_on_failure and proc.returncode != 0:
                print(f"[{name}] exited with code {proc.returncode}; restarting", file=sys.stderr)
                del self._procs[name]
                (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)
                self._spawn(pd)
                continue
            print(f"[{name}] exited with code {proc.returncode}; continuing without it", file=sys.stderr)
            del self._procs[name]
            (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)

    def _run_processes(self, defs: list[ProcessDefinition]) -> None:
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(_signum, _frame):
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        self.reload_request_file.unlink(missing_ok=True)
        for pd in defs:
            self._spawn(pd)

        defs_by_name = {pd.name: pd for pd in defs}
        while not self._stopping:
            self._reap_exited(defs_by_name)
            if not self._stopping:
                self._apply_reload_request(defs_by_name)
                time.sleep(0.5)

        self._stop_all()
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)

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
        """SIGTERM the whole group, then reap it, so the grace period is shared."""
        procs = [self._procs[name] for name in names if name in self._procs]
        for proc in procs:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    def _cleanup_proc_pid_files(self) -> None:
        for name in self._procs:
            (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)

    def _prod_process_definitions(self) -> list[ProcessDefinition]:
        return self._definitions.prod_process_definitions()

    def _process_definitions(self) -> list[ProcessDefinition]:
        return self._definitions.process_definitions()
