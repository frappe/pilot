"""This module houses the light-weight monitoring daemon that will utilise /proc
- Dump them into a bench-stats.json present inside the bench
- Logrotate the bench-stats.json file
"""

import getpass
import json
import os
import re
import subprocess
import typing
from datetime import datetime
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.loader import cli_root
from pilot.platform import is_linux
from pilot.utils import iter_sibling_benches, run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench, BenchConfig

# Oneshot `systemd --user` service driven by bench-monitor.timer (every 10s).
# Runs from the cli root so both `pilot` and `admin` import, using the admin
# venv's Python (which has psutil/pymysql).
MONITOR_TIMER_TEMPLATE = """\
[Unit]
Description=bench monitor timer

[Timer]
OnBootSec=10s
OnUnitInactiveSec=10s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

MONITOR_DAEMON_TEMPLATE = """\
[Unit]
Description=bench monitor

[Service]
Type=oneshot
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
ExecStart={python} -m pilot.core.monitor
StandardOutput=append:/var/log/bench-monitor.log
StandardError=append:/var/log/bench-monitor.error.log

[Install]
WantedBy=default.target
"""

SUPERVISOR_PROCESS_PATTERN = re.compile(r"^(?P<service>\S+)\s+RUNNING\s+pid\s+(?P<pid>\d+)", re.MULTILINE)
SYSTEMD_PID_PATTERN = re.compile(r"^MainPID=(?P<pid>\d+)", re.MULTILINE)


class ConfigureMonitor:
    """Installs a single system-wide `systemd --user` unit that monitors all benches."""

    def __init__(self):
        self.unit_name = "bench-monitor.service"
        self.timer_unit_name = "bench-monitor.timer"
        monitor_dir = cli_root() / "benches" / ".monitor"
        self.monitor_service_path = monitor_dir / self.unit_name
        self.monitor_timer_path = monitor_dir / self.timer_unit_name
        self.user_unit_dir = Path.home() / ".config" / "systemd" / "user"

    def _systemctl_env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def _render_unit(self) -> str:
        from pilot.managers.admin_env_manager import AdminEnvManager

        root = cli_root()
        return MONITOR_DAEMON_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )

    def _write_unit(self) -> None:
        self.monitor_service_path.parent.mkdir(parents=True, exist_ok=True)
        self.monitor_service_path.write_text(self._render_unit())

    def _install_user_unit(self) -> None:
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        link = self.user_unit_dir / self.unit_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(self.monitor_service_path.resolve())

    def _write_timer_unit(self) -> None:
        self.monitor_timer_path.parent.mkdir(parents=True, exist_ok=True)
        self.monitor_timer_path.write_text(MONITOR_TIMER_TEMPLATE)

    def _install_user_timer_unit(self) -> None:
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        link = self.user_unit_dir / self.timer_unit_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(self.monitor_timer_path.resolve())

    def install(self) -> None:
        self._write_unit()
        self._install_user_unit()
        self._write_timer_unit()
        self._install_user_timer_unit()

        # Keep the user manager running after logout so the timer survives, then
        # make sure it's up before talking to `systemctl --user`.
        subprocess.run(
            ["sudo", "loginctl", "enable-linger", getpass.getuser()],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["sudo", "systemctl", "start", f"user@{os.getuid()}.service"],
            capture_output=True,
            check=False,
        )

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        run_command(self._systemctl("enable", "--now", self.timer_unit_name), env=env)


class ToMonitor:
    def __init__(self, bench: "Bench"):
        self.bench = bench
        self.admin_service_name = f"{self.bench.config.name}-admin"

    def systemd_processes(self):
        from pilot.managers.process_managers.systemd import SystemdProcessManager

        bench_process_manager = SystemdProcessManager(self.bench)
        systemd_dir = self.bench.config_path / "systemd"

        if not systemd_dir.exists():
            return {}

        services = [service.name for service in systemd_dir.iterdir() if service.name.endswith(".service") and service.name != f"{self.admin_service_name}.service"]

        if not services:
            return {}

        pids = {}
        env = bench_process_manager._systemctl_env()
        cmd = bench_process_manager._systemctl("show", "--property", "MainPID", *services)

        output = run_command(cmd, env=env).stdout.decode().strip()
        pid_matches = SYSTEMD_PID_PATTERN.finditer(output)

        for service, match in zip(services, pid_matches):
            pid_val = int(match.group("pid"))
            if pid_val > 0:
                pids[service] = pid_val

        return pids

    def supervisord_processes(self):
        from pilot.managers.process_managers.supervisor import SupervisorProcessManager

        pids = {}
        bench_process_manager = SupervisorProcessManager(self.bench)

        result = run_command(["supervisorctl", "-c", bench_process_manager.supervisor_conf_path, "status"])
        supervised_processes = result.stdout.decode().strip()

        # Optimize matching loop by extracting specific named groups directly
        for match in SUPERVISOR_PROCESS_PATTERN.finditer(supervised_processes):
            service_name = match.group("service")

            if service_name != self.admin_service_name:
                pids[service_name] = int(match.group("pid"))

        return pids

    def to_monitor(self):
        prod_config = self.bench.config.production
        if not prod_config.enabled:
            return []

        manager_mapping = {"systemd": self.systemd_processes, "supervisor": self.supervisord_processes}

        monitor_func = manager_mapping.get(prod_config.process_manager)
        return monitor_func() if monitor_func else {}


class Monitor:
    """Implementation class for monitoring fetches and stores the details found in the proc dir"""

    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._cpu_snapshots: dict[int, tuple[int, int]] = {}
        self._system_cpu_snapshot: tuple[int, int] | None = None
        self.setup()

    @property
    def log_path(self) -> Path:
        from pilot.config.monitor_config import MonitorConfig

        # In case of benches that have not configured the monitoring path
        # We still want to log at the default monitoring path
        return self.bench.config.monitor.log_path or MonitorConfig.default_log_path(self.bench.config.name)

    @property
    def system_log_path(self) -> Path:
        return self.bench.config.monitor.system_log_path

    def setup_log_rotation(self) -> None:
        monitor_cfg = self.bench.config.monitor
        app_config = f"""\
{self.log_path} {{
    size {monitor_cfg.application_log_max_size}
    rotate 3
    compress
    missingok
    notifempty
    copytruncate
}}
"""
        subprocess.run(
            ["sudo", "tee", f"/etc/logrotate.d/{self.bench.config.name}-stats"],
            input=app_config.encode(),
            capture_output=True,
            check=True,
        )

        system_config = f"""\
{self.system_log_path} {{
    size {monitor_cfg.system_log_max_size}
    rotate 3
    compress
    missingok
    notifempty
    copytruncate
}}
"""
        subprocess.run(
            ["sudo", "tee", "/etc/logrotate.d/bench-system-stats"],
            input=system_config.encode(),
            capture_output=True,
            check=True,
        )

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Monitoring is only supported on linux based machines.")

        log_dir = self.log_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["sudo", "chown", f"{os.getuid()}:{os.getgid()}", str(log_dir)], check=True)
        self.setup_log_rotation()

    def _total_cpu_ticks(self) -> int:
        line = Path("/proc/stat").read_text().splitlines()[0]
        return sum(int(x) for x in line.split()[1:])

    def _cpu_percent(self, pid: int) -> float:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        cpu_ticks = int(fields[13]) + int(fields[14])
        total_ticks = self._total_cpu_ticks()
        prev = self._cpu_snapshots.get(pid)
        self._cpu_snapshots[pid] = (cpu_ticks, total_ticks)
        if prev is None:
            return 0.0
        delta_cpu = cpu_ticks - prev[0]
        delta_total = total_ticks - prev[1]
        return round(delta_cpu / delta_total * 100, 2) if delta_total > 0 else 0.0

    def _read_status(self, pid: int) -> dict[str, str]:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        return {k: v.strip() for k, v in (line.split(":", 1) for line in lines if ":" in line)}

    def _io_bytes(self, pid: int) -> tuple[int, int]:
        lines = Path(f"/proc/{pid}/io").read_text().splitlines()
        data = {k: int(v) for k, v in (line.split(": ", 1) for line in lines if ": " in line)}
        return data.get("read_bytes", 0), data.get("write_bytes", 0)

    def _open_fds(self, pid: int) -> int:
        try:
            return len(list(Path(f"/proc/{pid}/fd").iterdir()))
        except PermissionError:
            return -1

    def _process_metrics(self, service: str, pid: int) -> dict:
        status = self._read_status(pid)
        read_bytes, write_bytes = self._io_bytes(pid)
        return {
            "service": service,
            "pid": pid,
            "state": status.get("State", "?").split()[0],
            "cpu_percent": self._cpu_percent(pid),
            "memory_rss_mb": round(int(status.get("VmRSS", "0 kB").split()[0]) / 1024, 2),
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "open_fds": self._open_fds(pid),
        }

    def _load_average(self) -> tuple[float, float, float]:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0]), float(parts[1]), float(parts[2])

    def _system_cpu_percent(self) -> float:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        ticks = [int(x) for x in fields]
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        total = sum(ticks)
        prev = self._system_cpu_snapshot
        self._system_cpu_snapshot = (idle, total)
        if prev is None:
            return 0.0
        delta_idle = idle - prev[0]
        delta_total = total - prev[1]
        return round((1 - delta_idle / delta_total) * 100, 2) if delta_total > 0 else 0.0

    def _memory_usage(self) -> dict:
        data = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                data[key.strip()] = int(val.strip().split()[0])
        total_mb = round(data.get("MemTotal", 0) / 1024, 2)
        available_mb = round(data.get("MemAvailable", 0) / 1024, 2)
        used_mb = round(total_mb - available_mb, 2)
        return {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "available_mb": available_mb,
            "percent": round(used_mb / total_mb * 100, 2) if total_mb else 0.0,
        }

    def _disk_usage(self, path: Path) -> dict:
        st = os.statvfs(path)
        total_bytes = st.f_blocks * st.f_frsize
        free_bytes = st.f_bfree * st.f_frsize
        used_bytes = total_bytes - free_bytes
        return {
            "total_mb": round(total_bytes / 1024**2, 2),
            "used_mb": round(used_bytes / 1024**2, 2),
            "free_mb": round(free_bytes / 1024**2, 2),
            "percent": round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
        }

    def _zfs_pool_usage(self, pool: str) -> dict:
        result = subprocess.run(
            ["zpool", "list", "-H", "-p", "-o", "size,allocated,free", pool],
            capture_output=True, text=True, check=True,
        )
        size, allocated, free = (int(x) for x in result.stdout.strip().split())
        return {
            "pool": pool,
            "total_mb": round(size / 1024**2, 2),
            "used_mb": round(allocated / 1024**2, 2),
            "free_mb": round(free / 1024**2, 2),
            "percent": round(allocated / size * 100, 2) if size else 0.0,
        }

    def _find_zfs_pool(self) -> str | None:
        if self.bench.config.volume.enabled:
            return self.bench.config.volume.pool
        for _, config in iter_sibling_benches(self.bench.path):
            if config.volume.enabled:
                return config.volume.pool
        return None

    def _storage_usage(self) -> dict:
        result: dict = {"disk": self._disk_usage(self.bench.path)}
        pool = self._find_zfs_pool()
        if pool:
            result["zfs"] = self._zfs_pool_usage(pool)
        return result

    @property
    def is_system_log_authority(self):
        system_log_authority_path = self.bench.config.monitor.authority_file_path
        if not system_log_authority_path.exists():
            system_log_authority_path.write_text(self.bench.config.name)
            return True

        authority_bench = system_log_authority_path.read_text()
        if self.bench.config.name == authority_bench:
            return True

        for _, bench_config in iter_sibling_benches(self.bench.path):
            # In case the bench has been dropped or being used in dev mode
            # If that's not the case then the logging authority should remain with that bench.
            if bench_config.name == authority_bench and bench_config.production.process_manager in (
                "systemd",
                "supervisor",
            ):
                return False

        # We won't be using it for monitoring therefore update the monitoring authority
        system_log_authority_path.write_text(self.bench.config.name)
        return True

    def collect_system_metrics(self) -> None:
        if not self.is_system_log_authority:
            return
        metrics = {
            "load_avg": self._load_average(),
            "cpu_percent": self._system_cpu_percent(),
            "memory": self._memory_usage(),
            "storage": self._storage_usage(),
        }
        stats = json.loads(self.system_log_path.read_text()) if self.system_log_path.exists() else {}
        stats[datetime.now().isoformat()] = metrics
        self.system_log_path.write_text(json.dumps(stats, indent=2))

    def collect_application_metrics(self) -> None:
        processes = []

        for service, pid in ToMonitor(self.bench).to_monitor().items():
            if not Path(f"/proc/{pid}").exists():
                processes.append({"service": service, "pid": pid, "missing": True})
                continue
            processes.append(self._process_metrics(service, pid))

        metrics = {"bench": self.bench.config.name, "processes": processes}
        stats = json.loads(self.log_path.read_text()) if self.log_path.exists() else {}
        stats[datetime.now().isoformat()] = metrics
        self.log_path.write_text(json.dumps(stats, indent=2))


def resolve_monitor_log_path(bench_config: "BenchConfig"):
    from pilot.config.monitor_config import MonitorConfig

    bench_name = bench_config.name
    if bench_config.volume.enabled:
        from pilot.managers.volume_manager import VolumeManager

        volume = bench_config.volume
        VolumeManager(volume).create_dataset(f"{volume.pool}/logs")
        mountpoint = VolumeManager(volume).get_mountpoint(f"{volume.pool}/logs")
        return mountpoint / f"{bench_name}-stats.log"
    return MonitorConfig.default_log_path(bench_name)


def main() -> None:
    from pilot.core.bench import Bench

    # Sentinel path yields all benches in the benches/ directory
    sentinel = cli_root() / "benches" / ".monitor-placeholder"
    for bench_path, bench_config in iter_sibling_benches(sentinel):
        # Only monitor production enabled benches at all times
        if bench_config.production.enabled:
            monitor = Monitor(bench=Bench(bench_config, bench_path))
            monitor.collect_application_metrics()
            monitor.collect_system_metrics()


if __name__ == "__main__":
    main()
