from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessInfo:
    name: str
    status: str  # 'running' | 'stopped' | 'unknown'
    pid: int | None
    uptime: str | None
    log_file: Path


class ProcessReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read_all(self) -> list[ProcessInfo]:
        supervisor_conf = self._bench_root / "config" / "supervisor" / "supervisord.conf"
        if supervisor_conf.exists():
            return self._read_from_supervisor(supervisor_conf)
        return self._read_from_pids()

    def _bench_name(self) -> str:
        try:
            with open(self._bench_root / "bench.toml", "rb") as f:
                return tomllib.load(f).get("bench", {}).get("name", "bench")
        except Exception:
            return "bench"

    def _read_from_supervisor(self, conf_path: Path) -> list[ProcessInfo]:
        result = subprocess.run(
            ["supervisorctl", "-c", str(conf_path), "status"],
            capture_output=True,
            text=True,
        )
        bench_name = self._bench_name()
        processes = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            info = self._parse_supervisorctl_line(line, bench_name)
            if info:
                processes.append(info)
        return processes

    def _parse_supervisorctl_line(self, line: str, bench_name: str) -> ProcessInfo | None:
        # Format: "group:program-name    RUNNING   pid 1234, uptime 0:01:23"
        m = re.match(r"(\S+:\S+)\s+(\S+)\s*(.*)", line)
        if not m:
            return None

        full_name = m.group(1)   # e.g. "frappe:frappe-web"
        state = m.group(2).lower()
        rest = m.group(3)

        if state == "running":
            status = "running"
        elif state in ("stopped", "exited", "fatal", "backoff"):
            status = "stopped"
        else:
            status = "unknown"

        pid: int | None = None
        uptime: str | None = None
        pid_m = re.search(r"pid (\d+)", rest)
        if pid_m:
            pid = int(pid_m.group(1))
        uptime_m = re.search(r"uptime (\S+)", rest)
        if uptime_m:
            uptime = uptime_m.group(1)

        # Program part: "frappe-web" → strip bench_name prefix → "web"
        # Supervisor renames underscores to dashes, log files keep underscores.
        program = full_name.split(":", 1)[-1]  # "frappe-web"
        prefix = f"{bench_name}-"
        if program.startswith(prefix):
            program = program[len(prefix):]     # "web" or "worker-default-1"

        # Display name keeps dashes for readability; log file uses underscores.
        display_name = program
        log_name = program.replace("-", "_")    # "worker_default_1"
        log_file = self._bench_root / "logs" / f"{log_name}.log"

        return ProcessInfo(name=display_name, status=status, pid=pid, uptime=uptime, log_file=log_file)

    def _read_from_pids(self) -> list[ProcessInfo]:
        pids_dir = self._bench_root / "pids"
        if not pids_dir.exists():
            return []

        processes = []
        for pid_file in sorted(pids_dir.glob("*.pid")):
            name = pid_file.stem
            processes.append(self._read_process(name, pid_file))
        return processes

    def _read_process(self, name: str, pid_file: Path) -> ProcessInfo:
        log_file = self._bench_root / "logs" / f"{name}.log"
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return ProcessInfo(name=name, status="unknown", pid=None, uptime=None, log_file=log_file)

        try:
            os.kill(pid, 0)
            status = "running"
        except OSError:
            status = "stopped"

        return ProcessInfo(name=name, status=status, pid=pid, uptime=None, log_file=log_file)
