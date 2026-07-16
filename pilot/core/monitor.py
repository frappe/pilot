"""This module houses the light-weight monitoring daemon that will utilise /proc
- Dump them into a bench-stats.json present inside the bench
- Logrotate the bench-stats.json file
"""

import getpass
import json
import os
import re
import subprocess
import time
import typing
from datetime import datetime, timezone
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

# Gap between the two /proc samples used to turn cumulative CPU counters into a rate.
CPU_SAMPLE_INTERVAL = 1.0

# Order of the cumulative tick columns in the first line of /proc/stat.
CPU_STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")

# Loopback carries no real traffic; counting it would understate the share of
# actual network I/O in the total.
NET_IFACE_EXCLUDE = {"lo"}

# Whole-disk devices only, so partitions (sda1, nvme0n1p1, ...) aren't double-counted.
DISK_DEVICE_PATTERN = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+)$")
SECTOR_BYTES = 512


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
            return {}

        manager_mapping = {"systemd": self.systemd_processes, "supervisor": self.supervisord_processes}

        monitor_func = manager_mapping.get(prod_config.process_manager)
        return monitor_func() if monitor_func else {}


class Monitor:
    """Implementation class for monitoring fetches and stores the details found in the proc dir"""

    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._system_cpu: float = 0.0
        self._cpu_breakdown: dict[str, float] = {}
        self._proc_cpu: dict[int, float] = {}
        self._network: dict[str, float] = {}
        self._disk_io: dict[str, float] = {}
        self._targets: dict[str, int] | None = None
        self._cpu_before: tuple[dict[str, int], dict[int, int]] | None = None
        self._io_before: tuple[dict[str, int], dict[str, int]] | None = None
        self.setup()

    def monitored_targets(self) -> dict[str, int]:
        # `to_monitor()` shells out to systemctl/supervisorctl, so cache it: both
        # the CPU sampling and the application metrics reuse the same result.
        if self._targets is None:
            self._targets = ToMonitor(self.bench).to_monitor()
        return self._targets

    def sample_cpu(self) -> None:
        """Take the 'before' /proc reading. `main()` samples every bench, sleeps
        once, then computes — so N benches share a single sleep, not one each.
        CPU fields are cumulative, so this baseline is what makes them a rate."""
        pids = [pid for pid in self.monitored_targets().values() if Path(f"/proc/{pid}").exists()]
        self._cpu_before = (self._cpu_fields(), {pid: self._proc_ticks(pid) for pid in pids})

    def compute_cpu(self) -> None:
        fields_before, proc_before = self._cpu_before
        fields_after = self._cpu_fields()
        delta = {key: fields_after[key] - fields_before[key] for key in fields_after}
        delta_total = sum(delta.values())

        if delta_total > 0:
            pct = lambda ticks: round(ticks / delta_total * 100, 2)  # noqa: E731
            self._cpu_breakdown = {
                "user": pct(delta["user"] + delta["nice"]),
                "system": pct(delta["system"]),
                "iowait": pct(delta["iowait"]),
                "irq": pct(delta["irq"] + delta["softirq"]),
                "other": pct(delta["steal"]),
                "idle": pct(delta["idle"]),
            }
        else:
            # No measurable tick delta — report fully idle rather than fully busy.
            self._cpu_breakdown = {"user": 0.0, "system": 0.0, "iowait": 0.0, "irq": 0.0, "other": 0.0, "idle": 100.0}
        self._system_cpu = round(100 - self._cpu_breakdown["idle"], 2)
        self._proc_cpu = {pid: self._proc_usage(before, pid, delta_total) for pid, before in proc_before.items()}

    def _proc_usage(self, ticks_before: int, pid: int, delta_total: int) -> float:
        try:
            delta = self._proc_ticks(pid) - ticks_before
        except FileNotFoundError:
            return 0.0
        return round(delta / delta_total * 100, 2) if delta_total > 0 else 0.0

    def _cpu_fields(self) -> dict[str, int]:
        """First line of /proc/stat: cumulative tick counts per CPU state."""
        values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return dict(zip(CPU_STAT_FIELDS, values, strict=False))

    def sample_io(self) -> None:
        """Take the 'before' reading for network and disk throughput, sharing the
        same before/sleep/after window as `sample_cpu()`/`compute_cpu()`."""
        self._io_before = (self._net_fields(), self._disk_io_fields())

    def compute_io(self) -> None:
        net_before, disk_before = self._io_before
        net_after, disk_after = self._net_fields(), self._disk_io_fields()
        self._network = {
            "rx_bytes_per_sec": round((net_after["rx_bytes"] - net_before["rx_bytes"]) / CPU_SAMPLE_INTERVAL, 2),
            "tx_bytes_per_sec": round((net_after["tx_bytes"] - net_before["tx_bytes"]) / CPU_SAMPLE_INTERVAL, 2),
        }
        self._disk_io = {
            "read_bytes_per_sec": round((disk_after["read_bytes"] - disk_before["read_bytes"]) / CPU_SAMPLE_INTERVAL, 2),
            "write_bytes_per_sec": round((disk_after["write_bytes"] - disk_before["write_bytes"]) / CPU_SAMPLE_INTERVAL, 2),
        }

    def _net_fields(self) -> dict[str, int]:
        """/proc/net/dev: cumulative rx/tx bytes, summed across real interfaces."""
        rx = tx = 0
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            iface, rest = line.split(":", 1)
            if iface.strip() in NET_IFACE_EXCLUDE:
                continue
            values = rest.split()
            rx += int(values[0])
            tx += int(values[8])
        return {"rx_bytes": rx, "tx_bytes": tx}

    def _disk_io_fields(self) -> dict[str, int]:
        """/proc/diskstats: cumulative sectors read/written, summed across disks."""
        read_sectors = write_sectors = 0
        for line in Path("/proc/diskstats").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10 and DISK_DEVICE_PATTERN.match(fields[2]):
                read_sectors += int(fields[5])
                write_sectors += int(fields[9])
        return {"read_bytes": read_sectors * SECTOR_BYTES, "write_bytes": write_sectors * SECTOR_BYTES}

    def _proc_ticks(self, pid: int) -> int:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[13]) + int(fields[14])

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

    def _cpu_percent(self, pid: int) -> float:
        return self._proc_cpu.get(pid, 0.0)

    def _read_status(self, pid: int) -> dict[str, str]:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        return {k: v.strip() for k, v in (line.split(":", 1) for line in lines if ":" in line)}

    def _read_pss(self, pid: int) -> int:
        lines = Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
        for line in lines:
            if line.casefold().startswith("pss:"):
                parts = line.split(":", 1)[1].split()
                if parts:
                    return int(parts[0])
        return 0

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
        # Pss takes are of the shared memory pages giving a more accurate representation
        pss_memeory = self._read_pss(pid)
        read_bytes, write_bytes = self._io_bytes(pid)
        return {
            "service": service,
            "pid": pid,
            "state": status.get("State", "?").split()[0],
            "cpu_percent": self._cpu_percent(pid),
            "memory_rss_mb": round(pss_memeory / 1024, 2),
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "open_fds": self._open_fds(pid),
        }

    def _load_average(self) -> tuple[float, float, float]:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0]), float(parts[1]), float(parts[2])

    def _system_cpu_percent(self) -> float:
        return self._system_cpu

    def _system_cpu_breakdown(self) -> dict:
        return self._cpu_breakdown

    def _system_network(self) -> dict:
        return self._network

    def _system_disk_io(self) -> dict:
        return self._disk_io

    def _memory_usage(self) -> dict:
        data = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                data[key.strip()] = int(val.strip().split()[0])
        total_mb = round(data.get("MemTotal", 0) / 1024, 2)
        free_mb = round(data.get("MemFree", 0) / 1024, 2)
        cached_mb = round((data.get("Cached", 0) + data.get("Buffers", 0)) / 1024, 2)
        used_mb = round(max(total_mb - free_mb - cached_mb, 0), 2)
        swap_used_mb = round(max(data.get("SwapTotal", 0) - data.get("SwapFree", 0), 0) / 1024, 2)
        return {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "cached_mb": cached_mb,
            "free_mb": free_mb,
            "swap_used_mb": swap_used_mb,
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

    def _storage_usage(self) -> dict:
        return {"disk": self._disk_usage(self.bench.path)}

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
        self._append(
            self.system_log_path,
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "load_avg": self._load_average(),
                "cpu_percent": self._system_cpu_percent(),
                "cpu_breakdown": self._system_cpu_breakdown(),
                "memory": self._memory_usage(),
                "storage": self._storage_usage(),
                "network": self._system_network(),
                "disk_io": self._system_disk_io(),
            },
        )

    def collect_application_metrics(self) -> None:
        processes = []

        for service, pid in self.monitored_targets().items():
            if not Path(f"/proc/{pid}").exists():
                processes.append({"service": service, "pid": pid, "missing": True})
                continue
            processes.append(self._process_metrics(service, pid))

        self._append(
            self.log_path,
            {"time": datetime.now(timezone.utc).isoformat(), "bench": self.bench.config.name, "processes": processes},
        )

    @staticmethod
    def _append(path: Path, record: dict) -> None:
        # One compact JSON line per sample (JSON Lines). O(1) append — never reads
        # or rewrites the whole file, so cost and disk use don't grow with history.
        with path.open("a") as log_file:
            log_file.write(json.dumps(record) + "\n")


def resolve_monitor_log_path(bench_config: "BenchConfig"):
    from pilot.config.monitor_config import MonitorConfig

    return MonitorConfig.default_log_path(bench_config.name)


def _production_monitors() -> list[Monitor]:
    from pilot.core.bench import Bench

    # Sentinel path yields all benches in the benches/ directory
    sentinel = cli_root() / "benches" / ".monitor-placeholder"
    return [
        Monitor(bench=Bench(bench_config, bench_path))
        for bench_path, bench_config in iter_sibling_benches(
            sentinel,
        )
        if bench_config.production.enabled
    ]


def main() -> None:
    monitors = _production_monitors()
    # Sample every bench, sleep once, then compute: the single sleep is shared
    # across all N benches instead of paying CPU_SAMPLE_INTERVAL per bench.
    # systemd's OnUnitInactiveSec timer serialises runs, so none can overlap.
    for monitor in monitors:
        monitor.sample_cpu()
        monitor.sample_io()
    time.sleep(CPU_SAMPLE_INTERVAL)
    for monitor in monitors:
        monitor.compute_cpu()
        monitor.compute_io()
        monitor.collect_application_metrics()
        monitor.collect_system_metrics()


if __name__ == "__main__":
    main()
