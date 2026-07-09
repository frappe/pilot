from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass
class DiagnosticCheck:
    group: str
    name: str
    status: str
    detail: str
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {"group": self.group, "name": self.name, "status": self.status, "detail": self.detail}
        if self.hint:
            data["hint"] = self.hint
        return data


class DiagnosticRunner:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> list[DiagnosticCheck]:
        checks: list[DiagnosticCheck] = []
        checks.extend(self._bench_checks())
        checks.extend(self._configuration_checks())
        checks.extend(self._site_checks())
        checks.extend(self._dependency_checks())
        checks.extend(self._resource_checks())
        checks.extend(self._process_checks())
        checks.extend(self._redis_checks())
        checks.extend(self._database_checks())
        checks.extend(self._worker_checks())
        return checks

    def _bench_checks(self) -> list[DiagnosticCheck]:
        return [
            self._path_check("bench", "bench.toml", self.bench.path / "bench.toml", "Run bench new <name>."),
            self._path_check("bench", "python env", self.bench.python, "Run bench init."),
            self._path_check("bench", "apps directory", self.bench.apps_path, "Run bench init."),
            self._path_check("bench", "sites directory", self.bench.sites_path, "Run bench init."),
            self._path_check("bench", "config directory", self.bench.config_path, "Run bench setup config."),
        ]

    def _configuration_checks(self) -> list[DiagnosticCheck]:
        checks = [
            self._path_check("config", "common site config", self.bench.sites_path / "common_site_config.json", "Run bench setup config."),
            self._path_check("config", "procfile", self.bench.config_path / "Procfile", "Run bench setup config."),
        ]
        if self.bench.config.admin.enabled and not self.bench.config.admin.password:
            checks.append(DiagnosticCheck("config", "admin password", "fail", "admin is enabled without a password", "Run bench set-admin-password."))
        elif self.bench.config.admin.enabled:
            checks.append(DiagnosticCheck("config", "admin password", "ok", "configured"))
        else:
            checks.append(DiagnosticCheck("config", "admin", "warn", "admin UI is disabled"))
        return checks

    def _site_checks(self) -> list[DiagnosticCheck]:
        if not self.bench.sites_path.exists():
            return [DiagnosticCheck("sites", "sites", "fail", f"missing: {self.bench.sites_path}", "Run bench init.")]
        site_dirs = [p for p in sorted(self.bench.sites_path.iterdir()) if (p / "site_config.json").exists()]
        if not site_dirs:
            return [DiagnosticCheck("sites", "sites", "warn", "no sites found", "Create one with bench new-site <name>.")]
        checks = [DiagnosticCheck("sites", "site count", "ok", f"{len(site_dirs)} site(s) found")]
        checks.extend(self._site_config_check(site_dir) for site_dir in site_dirs)
        return checks

    def _dependency_checks(self) -> list[DiagnosticCheck]:
        checks = [
            self._executable_check("dependencies", "redis-server", "redis-server", "Install Redis or run bench init."),
            self._executable_check("dependencies", "node", "node", "Install Node.js or run bench init."),
        ]
        checks.append(self._chromium_check())
        return checks

    def _resource_checks(self) -> list[DiagnosticCheck]:
        return [
            self._cpu_check(),
            self._disk_check(),
            self._memory_check(),
        ]

    def _process_checks(self) -> list[DiagnosticCheck]:
        from pilot.managers.process_manager import ProcessManager

        try:
            manager = ProcessManager.detect_running(self.bench)
            workload = manager.is_running()
            admin = manager.is_admin_running()
        except Exception as exc:
            return [DiagnosticCheck("process", "process manager", "fail", str(exc))]
        if workload:
            return [DiagnosticCheck("process", "bench workload", "ok", "workload is running")]
        if admin:
            return [DiagnosticCheck("process", "admin", "warn", "admin is running, workload is stopped", "Run bench start or setup production.")]
        return [DiagnosticCheck("process", "bench workload", "fail", "bench is not running", "Run bench start.")]

    def _redis_checks(self) -> list[DiagnosticCheck]:
        redis = self.bench.config.redis
        return [
            self._port_check("redis", "cache", "127.0.0.1", redis.cache_port),
            self._port_check("redis", "queue", "127.0.0.1", redis.queue_port),
        ]

    def _database_checks(self) -> list[DiagnosticCheck]:
        db_type = self.bench.config.db_type
        if db_type == "sqlite":
            return [DiagnosticCheck("database", "sqlite", "skip", "sqlite has no shared database server")]
        if db_type == "postgres":
            pg = self.bench.config.postgres
            return [self._port_check("database", "postgres", pg.host, pg.port)]
        mdb = self.bench.config.mariadb
        if mdb.socket_path:
            return [self._socket_check("database", "mariadb socket", Path(mdb.socket_path))]
        return [self._port_check("database", "mariadb", mdb.host, mdb.port)]

    def _worker_checks(self) -> list[DiagnosticCheck]:
        workers = [p for p in self.bench.pids_path.glob("worker*.pid")] if self.bench.pids_path.exists() else []
        if workers:
            live = sum(1 for path in workers if self._pid_alive(path))
            status = "ok" if live else "fail"
            return [DiagnosticCheck("workers", "worker pids", status, f"{live}/{len(workers)} worker pid files are live")]
        if self.bench.config.production.use_companion_manager:
            return [DiagnosticCheck("workers", "workers", "skip", "workers run inside the companion web process")]
        return [DiagnosticCheck("workers", "worker pids", "warn", "no worker pid files found", "Start the bench and re-run diagnostics.")]

    def _disk_check(self) -> DiagnosticCheck:
        usage = shutil.disk_usage(self.bench.path)
        free_gb = usage.free / 1024**3
        used_pct = (usage.used / usage.total) * 100 if usage.total else 0
        detail = f"{free_gb:.1f} GB free, {used_pct:.0f}% used at {self.bench.path}"
        if free_gb < 1:
            return DiagnosticCheck("resources", "disk", "fail", detail, "Free disk space before running migrations or backups.")
        if free_gb < 5:
            return DiagnosticCheck("resources", "disk", "warn", detail, "Keep at least 5 GB free for updates and backups.")
        return DiagnosticCheck("resources", "disk", "ok", detail)

    def _cpu_check(self) -> DiagnosticCheck:
        try:
            load_1m = os.getloadavg()[0]
        except (AttributeError, OSError):
            return DiagnosticCheck("resources", "cpu load", "skip", "load average is not available")
        cpu_count = os.cpu_count() or 1
        detail = f"1m load {load_1m:.2f} across {cpu_count} CPU(s)"
        if load_1m > cpu_count * 2:
            return DiagnosticCheck("resources", "cpu load", "fail", detail, "Server is heavily loaded.")
        if load_1m > cpu_count:
            return DiagnosticCheck("resources", "cpu load", "warn", detail, "CPU pressure may slow requests and jobs.")
        return DiagnosticCheck("resources", "cpu load", "ok", detail)

    def _memory_check(self) -> DiagnosticCheck:
        memory = self._linux_memory()
        if not memory:
            return DiagnosticCheck("resources", "memory", "skip", "memory check is only available on Linux")
        available_gb = memory["available"] / 1024**2
        detail = f"{available_gb:.1f} GB available"
        if available_gb < 0.5:
            return DiagnosticCheck("resources", "memory", "fail", detail, "Free memory or add swap before starting workers.")
        if available_gb < 1:
            return DiagnosticCheck("resources", "memory", "warn", detail, "Low memory can make builds and migrations unreliable.")
        return DiagnosticCheck("resources", "memory", "ok", detail)

    def _path_check(self, group: str, name: str, path: Path, hint: str) -> DiagnosticCheck:
        if path.exists():
            return DiagnosticCheck(group, name, "ok", str(path))
        return DiagnosticCheck(group, name, "fail", f"missing: {path}", hint)

    def _port_check(self, group: str, name: str, host: str, port: int) -> DiagnosticCheck:
        if self._tcp_open(host, port):
            return DiagnosticCheck(group, name, "ok", f"{host}:{port} is reachable")
        return DiagnosticCheck(group, name, "fail", f"{host}:{port} is not reachable")

    def _socket_check(self, group: str, name: str, path: Path) -> DiagnosticCheck:
        if path.exists():
            return DiagnosticCheck(group, name, "ok", str(path))
        return DiagnosticCheck(group, name, "fail", f"missing: {path}", "Start the database service.")

    def _executable_check(self, group: str, name: str, executable: str, hint: str) -> DiagnosticCheck:
        path = shutil.which(executable)
        if path:
            return DiagnosticCheck(group, name, "ok", path)
        return DiagnosticCheck(group, name, "fail", f"{executable} not found in PATH", hint)

    def _chromium_check(self) -> DiagnosticCheck:
        candidates = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]
        found = next((path for name in candidates if (path := shutil.which(name))), "")
        if found:
            return DiagnosticCheck("dependencies", "chromium", "ok", found)
        return DiagnosticCheck("dependencies", "chromium", "warn", "not found in PATH", "PDF generation may fail until Chromium is installed.")

    def _site_config_check(self, site_dir: Path) -> DiagnosticCheck:
        path = site_dir / "site_config.json"
        try:
            json.loads(path.read_text())
        except Exception as exc:
            return DiagnosticCheck("sites", site_dir.name, "fail", f"invalid site_config.json: {exc}")
        return DiagnosticCheck("sites", site_dir.name, "ok", "site_config.json is valid")

    def _pid_alive(self, path: Path) -> bool:
        try:
            os.kill(int(path.read_text().strip()), 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            return False

    def _linux_memory(self) -> dict[str, int]:
        path = Path("/proc/meminfo")
        if not path.exists():
            return {}
        fields = {}
        for line in path.read_text().splitlines():
            key, value = line.split(":", 1)
            fields[key] = int(value.strip().split()[0])
        return {"available": fields.get("MemAvailable", 0)}

    def _tcp_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False


class DiagnosticReport:
    def __init__(self, bench_name: str, checks: list[DiagnosticCheck]) -> None:
        self.bench_name = bench_name
        self.checks = checks

    @property
    def failed(self) -> bool:
        return any(check.status == "fail" for check in self.checks)

    def to_json(self) -> str:
        data = {"bench": self.bench_name, "checks": [check.to_dict() for check in self.checks]}
        return json.dumps(data, indent=2)
