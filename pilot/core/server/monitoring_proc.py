from __future__ import annotations

import os
import re
from pathlib import Path

import psutil

CPU_STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
NET_IFACE_EXCLUDE = {"lo", "lo0"}
DISK_DEVICE_PATTERN = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|disk\d+)$")


class ProcMetricsReader:
    """Host and per-process metric reads. Counters a platform cannot report -
    PSS, per-process IO, and the iowait/irq/steal CPU slices outside Linux -
    read as zero rather than failing the sample."""

    def __init__(self, bench_path: Path):
        self.bench_path = bench_path

    def cpu_fields(self) -> dict[str, float]:
        """Cumulative CPU seconds per state. Only deltas are used, so seconds
        replace the raw jiffies the kernel exposes without changing any result."""
        times = psutil.cpu_times()
        return {field: float(getattr(times, field, 0.0)) for field in CPU_STAT_FIELDS}

    def proc_cpu_seconds(self, pid: int) -> float:
        times = psutil.Process(pid).cpu_times()
        return times.user + times.system

    def net_fields(self) -> dict[str, int]:
        counters = psutil.net_io_counters(pernic=True)
        interfaces = [
            counter for name, counter in counters.items() if name not in NET_IFACE_EXCLUDE
        ]
        return {
            "rx_bytes": sum(counter.bytes_recv for counter in interfaces),
            "tx_bytes": sum(counter.bytes_sent for counter in interfaces),
        }

    def disk_io_fields(self) -> dict[str, int]:
        """Whole disks only: partitions double-count their parent device, and
        dm-/loop devices double-count whatever they sit on."""
        counters = psutil.disk_io_counters(perdisk=True) or {}
        devices = [counter for name, counter in counters.items() if DISK_DEVICE_PATTERN.match(name)]
        return {
            "read_bytes": sum(counter.read_bytes for counter in devices),
            "write_bytes": sum(counter.write_bytes for counter in devices),
        }

    def process_state(self, pid: int) -> str:
        return psutil.Process(pid).status()

    def proc_memory_bytes(self, pid: int) -> int:
        """Proportional set size where the platform reports it, unique set size
        otherwise - both charge shared pages more honestly than RSS."""
        info = psutil.Process(pid).memory_full_info()
        shared_aware: int | None = getattr(info, "pss", None)
        if shared_aware is None:
            shared_aware = int(getattr(info, "uss", 0))
        return int(shared_aware)

    def io_bytes(self, pid: int) -> tuple[int, int]:
        process = psutil.Process(pid)
        if not hasattr(process, "io_counters"):
            return 0, 0
        try:
            counters = process.io_counters()
        except psutil.AccessDenied:
            return 0, 0
        return counters.read_bytes, counters.write_bytes

    def open_fds(self, pid: int) -> int:
        try:
            return psutil.Process(pid).num_fds()
        except psutil.AccessDenied:
            return -1

    def load_average(self) -> tuple[float, float, float]:
        one, five, fifteen = psutil.getloadavg()
        return one, five, fifteen

    def memory_usage(self) -> dict:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        total_bytes = int(memory.total)
        free_bytes = int(memory.free)
        cached_bytes = int(getattr(memory, "cached", 0) + getattr(memory, "buffers", 0))
        used_bytes = max(total_bytes - free_bytes - cached_bytes, 0)
        return {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "cached_bytes": cached_bytes,
            "free_bytes": free_bytes,
            "swap_used_bytes": int(swap.used),
            "percent": round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
        }

    def disk_usage(self, path: Path) -> dict:
        """statvfs, not psutil.disk_usage: psutil reports free space as the
        user-available blocks, which would drop the filesystem's reserved
        blocks out of total = used + free and move every recorded disk figure."""
        stats = os.statvfs(path)
        total_bytes = stats.f_blocks * stats.f_frsize
        free_bytes = stats.f_bfree * stats.f_frsize
        used_bytes = total_bytes - free_bytes
        return {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "percent": round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
        }

    def storage_usage(self) -> dict:
        return {"disk": self.disk_usage(self.bench_path)}
