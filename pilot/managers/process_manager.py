from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.loader import cli_root
from pilot.managers.admin_env_manager import AdminEnvManager
from pilot.managers.gunicorn_manager import GunicornManager

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


_COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[96m", "\033[92m", "\033[93m"]
_RESET = "\033[0m"


@dataclass
class ProcessDefinition:
    name: str
    command: str  # executable + args only - no `cd`, no inline env prefix
    log_file: Path
    env: dict = field(default_factory=dict)
    working_dir: Path | None = None  # was `cd {dir} &&`
    stop_timeout: int | None = None  # graceful-stop seconds (redis=300, web+companion=1600)


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
            from pilot.managers.process_managers.systemd import SystemdProcessManager

            return SystemdProcessManager(bench)
        from pilot.managers.process_managers.supervisor import SupervisorProcessManager

        return SupervisorProcessManager(bench)

    @classmethod
    def detect_running(cls, bench: "Bench") -> "ProcessManager":
        # Probe runtime state, not config presence, so a lingering config from a
        # switched manager can't mislead. Falls back to for_bench when none runs.
        from pilot.managers.process_managers.supervisor import SupervisorProcessManager
        from pilot.managers.process_managers.systemd import SystemdProcessManager

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

    def write_config(self) -> None:
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        lines = [f"{pd.name}: {pd.command}\n" for pd in self._process_definitions()]
        self.procfile_path.write_text("".join(lines))

    def _ensure_gunicorn_config(self) -> None:
        GunicornManager(self.bench).generate_config()

    def _ensure_redis_config(self) -> None:
        from pilot.managers.redis_manager import RedisManager

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

    def stop(self) -> None:
        if self.pid_file.exists():
            pid = int(self.pid_file.read_text().strip())
            self.pid_file.unlink(missing_ok=True)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                raise BenchError(f"Process {pid} is not running. Removed stale PID file.")
            return

        # No pid file (e.g. pre-init setup wizard): stop by port.
        config = self.bench.config
        pids = set()
        for port in (config.admin.port, config.http_port):
            pids |= _pids_listening(port)
        if not pids:
            raise BenchError("Bench is not running.")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

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
                pd.command,
                shell=True,
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

    def _prod_process_definitions(self) -> list[ProcessDefinition]:
        if self.bench.config.production.use_companion_manager:
            defs = [self._web_definition(), self._admin_definition()]
        elif self.bench.config.production.process_manager == "systemd":
            all_queues = ",".join(q for group in self.bench.config.workers.groups for q in group.queues)
            num_workers = sum(group.count for group in self.bench.config.workers.groups)
            worker_defs: list[ProcessDefinition] = [self._worker_pool_definition(all_queues, num_workers)]
            defs = [
                self._web_definition(),
                self._socketio_definition(),
                self._admin_definition(),
                *worker_defs,
            ]
        else:
            worker_defs = [pd for group in self.bench.config.workers.groups for pd in self._worker_definitions(",".join(group.queues), group.count)]
            defs = [
                self._web_definition(),
                self._socketio_definition(),
                self._admin_definition(),
                *worker_defs,
            ]
        defs.append(self._redis_definition("redis_cache", "redis_cache.conf"))
        defs.append(self._redis_definition("redis_queue", "redis_queue.conf"))
        return defs

    def _process_definitions(self) -> list[ProcessDefinition]:
        defs = [self._to_dev(pd) for pd in self._prod_process_definitions()]
        if self.bench.config.watch_apps_js:
            defs.append(self._watch_definition())
        if self.watch_admin_js:
            defs.append(self._admin_frontend_dev_definition())
        return defs

    def _to_dev(self, pd: ProcessDefinition) -> ProcessDefinition:
        """Map a production process definition to its dev-mode variant."""
        if pd.name == "admin" and self.watch_admin_js:
            return self._watch_admin_definition()
        if pd.name == "web":
            return self._web_definition(dev=True)
        return pd

    def _py_memory_env(self) -> dict:
        arenas = self.bench.config.gunicorn.malloc_arena_max
        if arenas and arenas > 0:
            return {"MALLOC_ARENA_MAX": str(arenas)}
        return {}

    def _web_definition(self, dev: bool = False) -> ProcessDefinition:
        sites = self.bench.sites_path
        python = self.bench.env_path / "bin" / "python"
        if dev:
            port = self.bench.config.http_port
            reload_flag = "" if self.bench.config.reload_python else " --noreload"
            return ProcessDefinition(
                name="web",
                command=f"{python} -m frappe.utils.bench_helper frappe serve --port {port}{reload_flag}",
                log_file=self.bench.logs_path / "web.log",
                env={"DEV_SERVER": "1"},
                working_dir=sites,
            )
        gunicorn = self.bench.env_path / "bin" / "gunicorn"
        companion = self.bench.config.production.use_companion_manager
        return ProcessDefinition(
            name="web",
            command=f"{gunicorn} -c ../config/gunicorn.conf.py frappe.app:application",
            log_file=self.bench.logs_path / "web.log",
            env=self._py_memory_env(),
            working_dir=sites,
            stop_timeout=1600 if companion else None,
        )

    def _socketio_definition(self) -> ProcessDefinition:
        if self.bench.config.socketio_backend == "python":
            python = self.bench.env_path / "bin" / "python"
            command = f"{python} -m frappe.realtime.server"
            working_dir = self.bench.path
            backend_env = self._py_memory_env()
        else:
            command = f"node {self.bench.apps_path}/frappe/socketio.js"
            working_dir = self.bench.sites_path
            backend_env = {}
        return ProcessDefinition(
            name="socketio",
            command=command,
            log_file=self.bench.logs_path / "socketio.log",
            env=backend_env,
            working_dir=working_dir,
        )

    def _watch_definition(self) -> ProcessDefinition:
        python = self.bench.env_path / "bin" / "python"
        return ProcessDefinition(
            name="watch",
            command=f"{python} -m frappe.utils.bench_helper frappe watch",
            log_file=self.bench.logs_path / "watch.log",
            working_dir=self.bench.sites_path,
        )

    def _worker_pool_definition(self, queues: str, num_workers: int) -> ProcessDefinition:
        python = self.bench.env_path / "bin" / "python"
        return ProcessDefinition(
            name="worker_pool",
            command=f"{python} -m frappe.utils.bench_helper frappe worker-pool --num-workers {num_workers} --queue {queues}",
            log_file=self.bench.logs_path / "worker_pool.log",
            env=self._py_memory_env(),
            working_dir=self.bench.sites_path,
        )

    def _worker_definitions(self, queue: str, count: int) -> list[ProcessDefinition]:
        sites = self.bench.sites_path
        python = self.bench.env_path / "bin" / "python"
        # Commas in queue names break supervisor's programs= list; slug them.
        slug = re.sub(r"[^A-Za-z0-9]+", "_", queue).strip("_") or "default"
        return [
            ProcessDefinition(
                name=f"worker_{slug}_{i}",
                command=f"{python} -m frappe.utils.bench_helper frappe worker --queue {queue}",
                log_file=self.bench.logs_path / f"worker_{slug}_{i}.log",
                env=self._py_memory_env(),
                working_dir=sites,
            )
            for i in range(1, count + 1)
        ]

    def _redis_definition(self, name: str, config_filename: str) -> ProcessDefinition:
        from pilot.managers.redis_manager import redis_server_binary

        return ProcessDefinition(
            name=name,
            command=f"{redis_server_binary() or 'redis-server'} {self.bench.config_path}/{config_filename}",
            log_file=self.bench.logs_path / f"{name}.log",
            stop_timeout=300,
        )

    def _admin_definition(self) -> ProcessDefinition:
        return self._build_admin_definition("--no-timeout")

    def _watch_admin_definition(self) -> ProcessDefinition:
        return self._build_admin_definition("--dev")

    def _build_admin_definition(self, mode_flag: str) -> ProcessDefinition:
        root = cli_root()
        python = AdminEnvManager(root).python
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin",
            command=f"{python} -m admin.backend.server --bench-root {self.bench.path} --port {cfg.port} --timeout {cfg.timeout} {mode_flag}",
            log_file=self.bench.logs_path / "admin.log",
            env={"PYTHONPATH": str(root)},
        )

    def _admin_frontend_dev_definition(self) -> ProcessDefinition:
        frontend_dir = cli_root() / "admin" / "frontend"
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin-ui",
            command=f"npm run dev --prefix {frontend_dir}",
            log_file=self.bench.logs_path / "admin-ui.log",
            env={"VITE_ADMIN_PORT": str(cfg.port)},
        )
