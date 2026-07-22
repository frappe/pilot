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
from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder
from pilot.utils import cli_root

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
            raise BenchError(f"Procfile not found at {self.procfile_path}. Run 'bench init' first.")
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

    def reload_workers(self, web_only: bool = False) -> None:
        pass

    def _run_processes(self, defs: list[ProcessDefinition]) -> None:
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(_signum, _frame):
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        for i, pd in enumerate(defs):
            proc = subprocess.Popen(
                pd.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
                cwd=str(pd.working_dir) if pd.working_dir else None,
                env={**os.environ, **pd.env} if pd.env else None,
            )
            color = _COLORS[i % len(_COLORS)]
            self._procs[pd.name] = proc
            (self.bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))
            threading.Thread(target=self._stream, args=(pd.name, proc, color), daemon=True).start()

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
        for proc in self._procs.values():
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
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

    def _to_dev(self, pd: ProcessDefinition) -> ProcessDefinition:
        return self._definitions.to_dev(pd)

    def _py_memory_env(self) -> dict:
        return self._definitions.py_memory_env()

    def _web_definition(self, dev: bool = False) -> ProcessDefinition:
        return self._definitions.web_definition(dev)

    def _socketio_definition(self) -> ProcessDefinition:
        return self._definitions.socketio_definition()

    def _watch_definition(self) -> ProcessDefinition:
        return self._definitions.watch_definition()

    def _worker_pool_definition(self, queues: str, num_workers: int) -> ProcessDefinition:
        return self._definitions.worker_pool_definition(queues, num_workers)

    def _worker_definitions(self, queue: str, count: int) -> list[ProcessDefinition]:
        return self._definitions.worker_definitions(queue, count)

    def _redis_definition(self, name: str, config_filename: str) -> ProcessDefinition:
        return self._definitions.redis_definition(name, config_filename)

    def _admin_definition(self) -> ProcessDefinition:
        return self._definitions.admin_definition()

    def _watch_admin_definition(self) -> ProcessDefinition:
        return self._definitions.watch_admin_definition()

    def _build_admin_definition(self, mode_flag: str) -> ProcessDefinition:
        return self._definitions.build_admin_definition(mode_flag)

    def _admin_frontend_dev_definition(self) -> ProcessDefinition:
        return self._definitions.admin_frontend_dev_definition()
