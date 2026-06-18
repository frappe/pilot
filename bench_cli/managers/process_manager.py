from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

from bench_cli.exceptions import BenchError
from bench_cli.managers.admin_env_manager import AdminEnvManager
from bench_cli.managers.gunicorn_manager import GunicornManager

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench

def _cli_root() -> Path:
    import bench_cli as _pkg

    return Path(_pkg.__file__).parent.parent


def _tcp_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is listening on host:port — a reliable liveness check
    for a foreground process that binds a known port."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[96m", "\033[92m", "\033[93m"]
_RESET = "\033[0m"


@dataclass
class ProcessDefinition:
    name: str
    command: str
    log_file: Path
    # Extra environment variables for this process (and anything it forks).
    env: dict = field(default_factory=dict)


class ProcessManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._procs: dict[str, subprocess.Popen] = {}
        self._stopping = False

    @property
    def procfile_path(self) -> Path:
        return self.bench.config_path / "Procfile"

    @property
    def pid_file(self) -> Path:
        return self.bench.pids_path / "bench.pid"

    # ── Config generation ───────────────────────────────────────────────────

    def generate_config(self) -> None:
        AdminEnvManager(_cli_root()).ensure()
        self._ensure_gunicorn_config()
        lines = [f"{pd.name}: {pd.command}\n" for pd in self._process_definitions()]
        self.procfile_path.write_text("".join(lines))

    def _ensure_gunicorn_config(self) -> None:
        GunicornManager(self.bench).generate_config()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return self.procfile_path.exists()

    def start(self) -> None:
        if not self.is_configured():
            raise BenchError(f"Procfile not found at {self.procfile_path}. Run 'bench init' first.")
        self.generate_config()
        self.pid_file.write_text(str(os.getpid()))
        try:
            self._run_processes(self._process_definitions())
        finally:
            self.pid_file.unlink(missing_ok=True)
            self._cleanup_proc_pid_files()

    def stop(self) -> None:
        if not self.pid_file.exists():
            raise BenchError("Bench is not running (no PID file found at pids/bench.pid).")
        pid = int(self.pid_file.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self.pid_file.unlink(missing_ok=True)
            raise BenchError(f"Process {pid} is not running. Removed stale PID file.")

    def is_running(self) -> bool:
        # The foreground runner writes its own pid to bench.pid and removes it on
        # exit — a live pid there is a reliable signal (pgrep on process names
        # matched unrelated processes system-wide).
        if not self.pid_file.exists():
            return False
        try:
            os.kill(int(self.pid_file.read_text().strip()), 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            return False

    def stop_admin(self) -> None:
        # Dev admin runs in the foreground Procfile group; stop() already ends it.
        pass

    def admin_is_running(self) -> bool:
        # In development the admin is served on admin.port by the foreground
        # runner; a bound port is the truth (not whether the runner pid is alive).
        return _tcp_port_open(self.bench.config.admin.port)

    def reload_web(self) -> None:
        pass

    # ── Procfile runner ─────────────────────────────────────────────────────

    def _run_processes(self, defs: List[ProcessDefinition]) -> None:
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(_signum, _frame):
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        for i, pd in enumerate(defs):
            proc = subprocess.Popen(
                pd.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
                env={**os.environ, **pd.env} if pd.env else None,
            )
            color = _COLORS[i % len(_COLORS)]
            self._procs[pd.name] = proc
            (self.bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))
            threading.Thread(target=self._stream, args=(pd.name, proc, color), daemon=True).start()

        while not self._stopping:
            for name, proc in list(self._procs.items()):
                if proc.poll() is not None:
                    print(f"[{name}] exited with code {proc.returncode}", file=sys.stderr)
                    self._stopping = True
                    break
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
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

    def _cleanup_proc_pid_files(self) -> None:
        for name in self._procs:
            (self.bench.pids_path / f"{name}.pid").unlink(missing_ok=True)

    # ── Process definitions ─────────────────────────────────────────────────

    def _prod_process_definitions(self) -> List[ProcessDefinition]:
        if self.bench.config.production.use_companion_manager:
            defs = [self._web_definition(), self._admin_definition()]
        elif self.bench.config.production.process_manager == "systemd":
            all_queues = ",".join(q for group in self.bench.config.workers.groups for q in group.queues)
            num_workers = sum(group.count for group in self.bench.config.workers.groups)
            worker_defs: List[ProcessDefinition] = [self._worker_pool_definition(all_queues, num_workers)]
            defs = [
                self._web_definition(),
                self._socketio_definition(),
                self._admin_definition(),
                *worker_defs,
            ]
        else:
            worker_defs = [
                pd
                for group in self.bench.config.workers.groups
                for pd in self._worker_definitions(",".join(group.queues), group.count)
            ]
            defs = [
                self._web_definition(),
                self._socketio_definition(),
                self._admin_definition(),
                *worker_defs,
            ]
        defs.append(self._redis_definition("redis_cache", "redis_cache.conf"))
        defs.append(self._redis_definition("redis_queue", "redis_queue.conf"))
        return defs

    def _process_definitions(self) -> List[ProcessDefinition]:
        defs = [self._to_dev(pd) for pd in self._prod_process_definitions()]
        defs.append(self._admin_frontend_dev_definition())
        return defs

    def _to_dev(self, pd: ProcessDefinition) -> ProcessDefinition:
        """Map a production process definition to its dev-mode variant."""
        if pd.name == "admin":
            return self._admin_dev_definition()
        if pd.name == "web":
            return self._web_definition(dev=True)
        return pd

    def _py_memory_env(self) -> dict:
        """Cap glibc malloc arenas for the Python procs to keep idle RSS down."""
        arenas = self.bench.config.gunicorn.malloc_arena_max
        if arenas and arenas > 0:
            return {"MALLOC_ARENA_MAX": str(arenas)}
        return {}

    def _web_definition(self, dev: bool = False) -> ProcessDefinition:
        sites = self.bench.sites_path
        python = self.bench.env_path / "bin" / "python"
        if dev:
            port = self.bench.config.http_port
            return ProcessDefinition(
                name="web",
                command=f"cd {sites} && DEV_SERVER=1 {python} -m frappe.utils.bench_helper frappe serve --port {port} --noreload",
                log_file=self.bench.logs_path / "web.log",
            )
        gunicorn = self.bench.env_path / "bin" / "gunicorn"
        return ProcessDefinition(
            name="web",
            command=f"cd {sites} && {gunicorn} -c ../config/gunicorn.conf.py frappe.app:application",
            log_file=self.bench.logs_path / "web.log",
            env=self._py_memory_env(),
        )

    def _socketio_definition(self) -> ProcessDefinition:
        if self.bench.config.socketio_backend == "python":
            python = self.bench.env_path / "bin" / "python"
            command = f"cd {self.bench.path} && {python} -m frappe.realtime.server"
            backend_env = self._py_memory_env()
        else:
            command = f"cd {self.bench.sites_path} && node {self.bench.apps_path}/frappe/socketio.js"
            backend_env = {}
        return ProcessDefinition(
            name="socketio",
            command=command,
            log_file=self.bench.logs_path / "socketio.log",
            env=backend_env,
        )

    def _worker_pool_definition(self, queues: str, num_workers: int) -> ProcessDefinition:
        sites = self.bench.sites_path
        return ProcessDefinition(
            name="worker_pool",
            command=f"cd {sites} && {self.bench.env_path}/bin/python -m frappe.utils.bench_helper frappe worker-pool --num-workers {num_workers} --queue {queues}",
            log_file=self.bench.logs_path / "worker_pool.log",
            env=self._py_memory_env(),
        )

    def _worker_definitions(self, queue: str, count: int) -> List[ProcessDefinition]:
        sites = self.bench.sites_path
        # `queue` may be a comma-separated list (a worker group can serve several
        # queues). Commas are illegal in a process/unit name — they break
        # supervisor's comma-delimited `programs=` list — so slug them for the
        # name and log file while keeping the real list in the --queue argument.
        import re

        slug = re.sub(r"[^A-Za-z0-9]+", "_", queue).strip("_") or "default"
        return [
            ProcessDefinition(
                name=f"worker_{slug}_{i}",
                command=f"cd {sites} && {self.bench.env_path}/bin/python -m frappe.utils.bench_helper frappe worker --queue {queue}",
                log_file=self.bench.logs_path / f"worker_{slug}_{i}.log",
                env=self._py_memory_env(),
            )
            for i in range(1, count + 1)
        ]

    def _redis_definition(self, name: str, config_filename: str) -> ProcessDefinition:
        return ProcessDefinition(
            name=name,
            command=f"redis-server {self.bench.config_path}/{config_filename}",
            log_file=self.bench.logs_path / f"{name}.log",
        )

    def _admin_definition(self) -> ProcessDefinition:
        cli_root = _cli_root()
        python = AdminEnvManager(cli_root).python
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin",
            command=(f"PYTHONPATH={cli_root} {python} -m admin.backend.server --bench-root {self.bench.path} --port {cfg.port} --timeout {cfg.timeout} --no-timeout"),
            log_file=self.bench.logs_path / "admin.log",
        )

    def _admin_dev_definition(self) -> ProcessDefinition:
        cli_root = _cli_root()
        python = AdminEnvManager(cli_root).python
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin",
            command=(f"PYTHONPATH={cli_root} {python} -m admin.backend.server --bench-root {self.bench.path} --port {cfg.port} --timeout {cfg.timeout} --dev"),
            log_file=self.bench.logs_path / "admin.log",
        )

    def _admin_frontend_dev_definition(self) -> ProcessDefinition:
        cli_root = _cli_root()
        frontend_dir = cli_root / "admin" / "frontend"
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin-ui",
            command=f"VITE_ADMIN_PORT={cfg.port} npm run dev --prefix {frontend_dir}",
            log_file=self.bench.logs_path / "admin-ui.log",
        )


class ProcessManagerFactory:
    @staticmethod
    def create(bench: "Bench") -> ProcessManager:
        if not bench.config.production.enabled:
            return ProcessManager(bench)

        from bench_cli.managers.systemd_process_manager import SystemdProcessManager
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager

        if bench.config.production.process_manager == "systemd":
            return SystemdProcessManager(bench)

        return SupervisorProcessManager(bench)

    @classmethod
    def detect_running(cls, bench: "Bench") -> ProcessManager:
        """Return the process manager that is currently active for this bench.

        Probes actual runtime state — systemd is-active and supervisord
        pid + supervisorctl status — rather than config file presence.
        This avoids confusion when config files linger after switching managers.
        Falls back to create() when nothing is detected as running, so callers
        still get the appropriate "not running" error for the configured manager.
        """
        from bench_cli.managers.systemd_process_manager import SystemdProcessManager
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager

        systemd = SystemdProcessManager(bench)
        if systemd.is_running():
            return systemd

        supervisor = SupervisorProcessManager(bench)
        if supervisor.is_running():
            return supervisor

        return cls.create(bench)
