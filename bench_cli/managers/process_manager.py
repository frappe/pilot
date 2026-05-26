from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List

from bench_cli.exceptions import BenchError
from bench_cli.managers.admin_env_manager import AdminEnvManager

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


def _cli_root() -> Path:
    import bench_cli as _pkg
    return Path(_pkg.__file__).parent.parent

_COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[96m", "\033[92m", "\033[93m"]
_RESET = "\033[0m"


@dataclass
class ProcessDefinition:
    name: str
    command: str
    log_file: Path


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
        lines = [f"{pd.name}: {pd.command}\n" for pd in self._process_definitions()]
        self.procfile_path.write_text("".join(lines))

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        self.pid_file.write_text(str(os.getpid()))
        try:
            self._run_procfile()
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
        process_names = [pd.name for pd in self._process_definitions()]
        pattern = "|".join(process_names)
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
        return bool(result.stdout.strip())

    # ── Procfile runner ─────────────────────────────────────────────────────

    def _run_procfile(self) -> None:
        entries = self._parse_procfile()

        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _stop(signum, frame):
            self._stopping = True
            self._stop_all()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        for i, (name, command) in enumerate(entries):
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            color = _COLORS[i % len(_COLORS)]
            self._procs[name] = proc
            (self.bench.pids_path / f"{name}.pid").write_text(str(proc.pid))
            threading.Thread(target=self._stream, args=(name, proc, color), daemon=True).start()

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

    def _parse_procfile(self) -> list[tuple[str, str]]:
        entries = []
        for line in self.procfile_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, command = line.partition(":")
            entries.append((name.strip(), command.strip()))
        return entries

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

    def _process_definitions(self) -> List[ProcessDefinition]:
        definitions = [
            self._web_definition(),
            self._socketio_definition(),
            self._admin_definition(),
            *self._worker_definitions("default", self.bench.config.workers.default_count),
            *self._worker_definitions("short", self.bench.config.workers.short_count),
            *self._worker_definitions("long", self.bench.config.workers.long_count),
        ]
        if self.bench.config.redis.is_single_instance:
            definitions.append(self._redis_definition("redis", "redis.conf"))
        else:
            definitions.append(self._redis_definition("redis_cache", "redis_cache.conf"))
            definitions.append(self._redis_definition("redis_queue", "redis_queue.conf"))
            definitions.append(self._redis_definition("redis_socketio", "redis_socketio.conf"))
        return definitions

    def _web_definition(self) -> ProcessDefinition:
        port = self.bench.config.http_port
        sites = self.bench.sites_path
        bench_bin = self.bench.env_path / "bin" / "bench"
        return ProcessDefinition(
            name="web",
            command=f"cd {sites} && {bench_bin} frappe serve --port {port} --noreload",
            log_file=self.bench.logs_path / "web.log",
        )

    def _socketio_definition(self) -> ProcessDefinition:
        sites = self.bench.sites_path
        return ProcessDefinition(
            name="socketio",
            command=f"cd {sites} && node {self.bench.apps_path}/frappe/socketio.js",
            log_file=self.bench.logs_path / "socketio.log",
        )

    def _worker_definitions(self, queue: str, count: int) -> List[ProcessDefinition]:
        sites = self.bench.sites_path
        return [
            ProcessDefinition(
                name=f"worker_{queue}_{i}",
                command=f"cd {sites} && {self.bench.env_path}/bin/bench frappe worker --queue {queue}",
                log_file=self.bench.logs_path / f"worker_{queue}_{i}.log",
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
        command = (
            f"PYTHONPATH={cli_root} {python} -m admin.backend.server"
            f" --bench-root {self.bench.path}"
            f" --port {cfg.port}"
            f" --timeout {cfg.timeout}"
        )
        return ProcessDefinition(
            name="admin",
            command=command,
            log_file=self.bench.logs_path / "admin.log",
        )


class ProcessManagerFactory:
    @staticmethod
    def create(bench: "Bench") -> ProcessManager:
        return ProcessManager(bench)
