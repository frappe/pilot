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
    rss_mb: float | None = None
    pss_mb: float | None = None


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _proc_uptime(pid: int) -> str | None:
    """Wall-clock uptime of a process from its /proc start time.

    Works for any manager (systemd/supervisor/dev) since it only needs the
    PID; returns None if the process is gone or /proc is unreadable.
    """
    try:
        with open("/proc/uptime") as f:
            system_uptime = float(f.read().split()[0])
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # starttime is field 22 (clock ticks since boot); fields after comm ')'
        # start at field 3, so field 22 is index 19 here.
        starttime_ticks = int(data[data.rindex(")") + 2:].split()[19])
        elapsed = system_uptime - starttime_ticks / os.sysconf("SC_CLK_TCK")
        return _format_duration(elapsed) if elapsed >= 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _read_pss_kb(pid: int) -> int | None:
    """Proportional Set Size in KB from /proc/<pid>/smaps_rollup (Linux 4.14+).

    Returns None if the kernel lacks smaps_rollup or we can't read it (e.g.
    the process belongs to another user).
    """
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _subtree_pids(pid: int) -> list[int]:
    """The pid plus all of its descendant pids.

    gunicorn/supervisord run their workers as children of the main PID, so a
    service's real footprint is the whole subtree — not just MainPID, which is
    all systemd/supervisor hand us.
    """
    children: dict[int, list[int]] = {}
    try:
        proc_entries = os.listdir("/proc")
    except OSError:
        return [pid]
    for entry in proc_entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                data = f.read()
            # comm (2nd field) may contain spaces/parens; ppid is the 2nd field after ')'
            ppid = int(data[data.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))

    tree, stack = [], [pid]
    while stack:
        cur = stack.pop()
        tree.append(cur)
        stack.extend(children.get(cur, []))
    return tree


def _get_process_stats(pid: int) -> tuple[float | None, float | None, float | None]:
    """Aggregate CPU%, RSS (MB) and PSS (MB) across the whole process subtree."""
    pids = _subtree_pids(pid)
    try:
        result = subprocess.run(
            ["ps", "-o", "%cpu=,rss=", "-p", ",".join(map(str, pids))],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None, None, None
    if result.returncode != 0:
        return None, None, None

    cpu_total, rss_kb = 0.0, 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            cpu_total += float(parts[0])
            rss_kb += int(parts[1])

    pss_vals = [v for p in pids if (v := _read_pss_kb(p)) is not None]
    pss_mb = round(sum(pss_vals) / 1024.0, 1) if pss_vals else None
    return round(cpu_total, 1), round(rss_kb / 1024.0, 1), pss_mb


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
        running = bool(pid and status == "running")
        cpu, rss, pss = _get_process_stats(pid) if running else (None, None, None)
        uptime = _proc_uptime(pid) if running else None
        return ProcessInfo(name=name, status=status, pid=pid, uptime=uptime, log_file=log_file, cpu_percent=cpu, rss_mb=rss, pss_mb=pss)

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
        if pid_m := re.search(r"pid (\d+)", rest):
            pid = int(pid_m.group(1))

        program = full_name.split(":", 1)[-1].removeprefix(f"{bench_name}-")
        log_file = self._bench_root / "logs" / f"{program.replace('-', '_')}.log"
        running = bool(pid and status == "running")
        cpu, rss, pss = _get_process_stats(pid) if running else (None, None, None)
        uptime = _proc_uptime(pid) if running else None
        return ProcessInfo(name=program, status=status, pid=pid, uptime=uptime, log_file=log_file, cpu_percent=cpu, rss_mb=rss, pss_mb=pss)

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

        cpu, rss, pss = _get_process_stats(pid) if status == "running" else (None, None, None)
        uptime = _proc_uptime(pid) if status == "running" else None
        return ProcessInfo(name=name, status=status, pid=pid, uptime=uptime, log_file=log_file, cpu_percent=cpu, rss_mb=rss, pss_mb=pss)
