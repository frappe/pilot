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

from pilot.exceptions import BenchError
from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder, ProcessState

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def _live_pid(pid_file: Path) -> int | None:
    """The pid in the file while that process is alive."""
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return None
    return pid


def _tcp_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Whether something listens on the port."""
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






class ProcessManager:
    # Redis holds the job queue, and the admin plane is what issues the reload.
    # Both must survive it, so only app-code processes are restarted.
    non_reloadable = frozenset({"admin", "admin-ui", "redis_cache", "redis_queue", "watch"})
    # Stopped last, so their clients are already gone and cannot log a lost connection.
    datastores = frozenset({"redis_cache", "redis_queue"})
    stop_grace_seconds = 5
    colors = (
        "\033[36m",
        "\033[32m",
        "\033[33m",
        "\033[35m",
        "\033[34m",
        "\033[96m",
        "\033[92m",
        "\033[93m",
    )
    reset = "\033[0m"

    def __init__(self, bench: "Bench", watch_admin_js: bool | None = None) -> None:
        self.bench = bench
        self.watch_admin_js = bench.config.watch_admin_js if watch_admin_js is None else watch_admin_js
        self._procs: dict[str, subprocess.Popen] = {}
        self._colors: dict[str, str] = {}
        self._stopping = False

    @classmethod
    def for_bench(cls, bench: "Bench") -> "ProcessManager":
        """The process manager for the bench's production setting."""
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
        """The process manager that runs the bench now."""
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
        """The Procfile."""
        return self.bench.config_path / "Procfile"

    @property
    def pid_file(self) -> Path:
        """The PID file of pilot start."""
        return self.bench.pids_path / "bench.pid"

    @property
    def reload_request_file(self) -> Path:
        """The file that asks pilot start to reload workers."""
        return self.bench.pids_path / "reload.request"

    @property
    def python(self) -> Path:
        """The bench Python."""
        return self.bench.env_path / "bin" / "python"

    @property
    def _definitions(self) -> ProcessDefinitionBuilder:
        """The process definition builder."""
        return ProcessDefinitionBuilder(self.bench, self.python, self.watch_admin_js)

    def write_config(self) -> None:
        """Write the Procfile and the configs the processes need."""
        from pilot.core.server import Server

        definitions = self._process_definitions()
        if any(pd.name == "admin" for pd in definitions):
            AdminEnvManager(Server().root).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        lines = [f"{pd.name}: {shlex.join(pd.argv_with_hooks)}\n" for pd in definitions]
        self.procfile_path.write_text("".join(lines))

    def _ensure_gunicorn_config(self) -> None:
        """Write the gunicorn config."""
        GunicornManager(self.bench).generate_config()

    def _ensure_redis_config(self) -> None:
        """Write the Redis configs."""
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()

    def is_configured(self) -> bool:
        """Whether the Procfile exists."""
        return self.procfile_path.exists()

    def start(self) -> None:
        """Run the processes in the foreground; raise when there is no Procfile."""
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
        """Run the processes in the foreground."""
        self.start()

    def stop(self) -> None:
        """Stop pilot start and its processes."""
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
        """Whether pilot start runs."""
        return _live_pid(self.pid_file) is not None

    def stop_admin(self) -> None:
        """Do nothing; pilot start runs the Admin with the workload."""
        pass

    def restart(self) -> None:
        """Do nothing; development has no restart."""
        pass

    def restart_admin(self) -> None:
        """Do nothing; development has no Admin restart."""
        pass

    def wake_admin(self) -> None:
        """Start an Admin that stopped on idle. Only a socket-activated Admin stops."""

    def is_admin_running(self) -> bool:
        """Whether the Admin port is open."""
        return _tcp_port_open(self.bench.config.admin.port)

    def get_process_states(self, definitions: list[ProcessDefinition]) -> dict[str, ProcessState]:
        """Read from the pid files the dev runner writes for each process."""
        states = {}
        for definition in definitions:
            pid = _live_pid(self.bench.pids_path / f"{definition.name}.pid")
            states[definition.name] = ProcessState("running", pid) if pid else ProcessState("stopped")
        return states

    def restart_processes(self, definitions: list[ProcessDefinition]) -> None:
        """Raise; only production can restart one process."""
        raise BenchError("Restarting one process needs a production bench; restart `pilot start` instead.")

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
        with contextlib.suppress(BenchError, OSError):
            self.bench.clear_cache()

    def _apply_reload_request(self, defs_by_name: dict[str, ProcessDefinition]) -> None:
        """Restart the processes a queued reload asked for, leaving admin alone."""
        try:
            scope = self.reload_request_file.read_text().strip()
        except OSError:
            return
        self.reload_request_file.unlink(missing_ok=True)
        names = ["web"] if scope == "web" else [n for n in self._procs if n not in self.non_reloadable]
        for name in names:
            definition = defs_by_name.get(name)
            if definition is None or name not in self._procs:
                continue
            print(f"[{name}] reloading", file=sys.stderr)
            self._terminate(self._procs[name])
            self._spawn(definition)

    def _terminate(self, proc: subprocess.Popen) -> None:
        """Stop a process group, and kill it when it does not stop."""
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=self.stop_grace_seconds)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    def _spawn(self, pd: ProcessDefinition) -> None:
        """Start a process and stream its output."""
        try:
            proc = subprocess.Popen(
                pd.argv_with_hooks,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
                cwd=str(pd.working_dir) if pd.working_dir else None,
                env={**os.environ, **pd.env} if pd.env else None,
            )
        except OSError as exc:
            if pd.critical:
                raise
            print(f"[{pd.name}] failed to start: {exc}; continuing without it", file=sys.stderr)
            return
        self._procs[pd.name] = proc
        (self.bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))
        threading.Thread(target=self._stream, args=(pd.name, proc, self._color(pd.name)), daemon=True).start()

    def _color(self, name: str) -> str:
        """The color of a process name."""
        return self._colors.setdefault(name, self.colors[len(self._colors) % len(self.colors)])

    def _run_processes(self, defs: list[ProcessDefinition]) -> None:
        """Run the processes until one exits or a signal arrives."""
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(_signum, _frame):
            """Stop every process."""
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        self.reload_request_file.unlink(missing_ok=True)
        defs_by_name = {pd.name: pd for pd in defs}
        is_critical = {pd.name: pd.critical for pd in defs}
        # finally also stops what already started when a critical process cannot launch.
        try:
            for pd in defs:
                self._spawn(pd)

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
        finally:
            self._stop_all()
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)

    def _stream(self, name: str, proc: subprocess.Popen, color: str) -> None:
        """Print the process output with a colored prefix."""
        assert proc.stdout is not None
        prefix = f"{color}[{name}]{self.reset} "
        for raw in proc.stdout:
            sys.stdout.write(prefix + raw.decode(errors="replace") + self.reset)
            sys.stdout.flush()

    def _stop_all(self) -> None:
        """Drain the workload before redis. Killing them together leaves the workers
        and the realtime bridge retrying a socket that is already closed."""
        names = list(self._procs)
        self._stop_group([name for name in names if name not in self.datastores])
        self._stop_group([name for name in names if name in self.datastores])
        self.reload_request_file.unlink(missing_ok=True)

    def _stop_group(self, names: list[str]) -> None:
        """SIGTERM the whole group, then reap it, so the grace period is shared."""
        procs = [self._procs[name] for name in names if name in self._procs]
        for proc in procs:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=self.stop_grace_seconds)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    def _cleanup_proc_pid_files(self) -> None:
        """Remove the process PID files."""
        for name in self._procs:
            (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)

    @property
    def process_definitions(self) -> list[ProcessDefinition]:
        """The processes this manager runs."""
        return self._process_definitions()

    def _prod_process_definitions(self) -> list[ProcessDefinition]:
        """The production process definitions."""
        return self._definitions.prod_process_definitions()

    def _process_definitions(self) -> list[ProcessDefinition]:
        """The development process definitions."""
        return self._definitions.process_definitions()
