from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager


@dataclass
class ProcessInfo:
    name: str
    status: str  # 'running' | 'stopped' | 'unknown'
    pid: int | None
    uptime: str | None
    log_file: Path
    cpu_percent: float | None = None
    memory_mb: float | None = None


def _get_process_stats(pid: int) -> tuple[float | None, float | None]:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu=,rss="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None, None
        parts = result.stdout.strip().split()
        if len(parts) >= 2:
            return float(parts[0]), round(int(parts[1]) / 1024.0, 1)
    except Exception:
        pass
    return None, None


class ProcessReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read_all(self) -> list[ProcessInfo]:
        from bench_cli.config.bench_config import BenchConfig
        from bench_cli.core.bench import Bench
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
        from bench_cli.managers.systemd_process_manager import SystemdProcessManager

        # If the bench config file is not present there is no point in look at procs
        config = BenchConfig.from_file(self._bench_root / "bench.toml")
        bench = Bench(config, self._bench_root)
        systemd = SystemdProcessManager(bench)
        supervisor = SupervisorProcessManager(bench)
        if systemd.is_running():
            return self._read_from_systemd(systemd)
        if supervisor.is_running():
            return self._read_from_supervisor(supervisor)

        return self._read_from_pids()

    # ── Systemd ──────────────────────────────────────────────────────────────

    def _read_from_systemd(self, systemd: "SystemdProcessManager") -> list[ProcessInfo]:
        bench_name = systemd.bench.config.name
        units = [f.name for f in sorted(systemd.user_unit_dir.glob(f"{bench_name}-*.service"))]
        if not units:
            return []
        result = subprocess.run(
            [*systemd._systemctl("show", *units), "--property=Id,ActiveState,MainPID"],
            capture_output=True,
            text=True,
            env=systemd._systemctl_env(),
        )
        return [info for block in result.stdout.strip().split("\n\n") if (info := self._parse_systemd_block(block.strip(), bench_name))]

    def _parse_systemd_block(self, block: str, bench_name: str) -> ProcessInfo | None:
        props = dict(line.partition("=")[::2] for line in block.splitlines() if "=" in line)
        unit_id = props.get("Id", "")
        if not unit_id.endswith(".service"):
            return None

        name = unit_id.removesuffix(".service").removeprefix(f"{bench_name}-")
        state = props.get("ActiveState", "")
        status = "running" if state == "active" else ("stopped" if state in ("inactive", "failed", "deactivating") else "unknown")
        pid_str = props.get("MainPID", "0")
        pid = int(pid_str) if pid_str.isdigit() and pid_str != "0" else None
        log_file = self._bench_root / "logs" / f"{name}.log"
        cpu, mem = _get_process_stats(pid) if pid and status == "running" else (None, None)
        return ProcessInfo(name=name, status=status, pid=pid, uptime=None, log_file=log_file, cpu_percent=cpu, memory_mb=mem)

    # ── Supervisor ───────────────────────────────────────────────────────────

    def _read_from_supervisor(self, supervisor: SupervisorProcessManager) -> list[ProcessInfo]:
        bench_name = supervisor.bench.config.name
        result = subprocess.run(
            [*supervisor._supervisorctl(), "status"],
            capture_output=True,
            text=True,
        )
        return [
            info
            for line in result.stdout.splitlines()
            if line.strip()
            and (
                info := self._parse_supervisorctl_line(
                    line.strip(),
                    bench_name
                )
            )
        ]

    def _parse_supervisorctl_line(self, line: str, bench_name: str) -> ProcessInfo | None:
        m = re.match(r"(\S+:\S+)\s+(\S+)\s*(.*)", line)
        if not m:
            return None

        full_name, state, rest = m.group(1), m.group(2).lower(), m.group(3)
        status = "running" if state == "running" else ("stopped" if state in ("stopped", "exited", "fatal", "backoff") else "unknown")

        pid: int | None = None
        uptime: str | None = None
        if pid_m := re.search(r"pid (\d+)", rest):
            pid = int(pid_m.group(1))
        if uptime_m := re.search(r"uptime (\S+)", rest):
            uptime = uptime_m.group(1)

        program = full_name.split(":", 1)[-1].removeprefix(f"{bench_name}-")
        log_file = self._bench_root / "logs" / f"{program.replace('-', '_')}.log"
        cpu, mem = _get_process_stats(pid) if pid and status == "running" else (None, None)
        return ProcessInfo(name=program, status=status, pid=pid, uptime=uptime, log_file=log_file, cpu_percent=cpu, memory_mb=mem)

    # ── Procfile (dev) ───────────────────────────────────────────────────────

    def _read_from_pids(self) -> list[ProcessInfo]:
        pids_dir = self._bench_root / "pids"
        if not pids_dir.exists():
            return []
        return [self._read_process(f.stem, f) for f in sorted(pids_dir.glob("*.pid"))]

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

        cpu, mem = _get_process_stats(pid) if status == "running" else (None, None)
        return ProcessInfo(name=name, status=status, pid=pid, uptime=None, log_file=log_file, cpu_percent=cpu, memory_mb=mem)
