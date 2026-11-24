from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

# Gap between the two readings that turn cumulative counters into a rate.
CPU_SAMPLE_INTERVAL = 1.0
CPU_STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
NET_IFACE_EXCLUDE = {"lo", "lo0"}
DISK_DEVICE_PATTERN = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|disk\d+)$")


class ProcMetricsReader:
    """Host and per-process metric reads. Counters a platform cannot report -
    PSS, per-process IO, and the iowait/irq/steal CPU slices outside Linux -
    read as zero rather than failing the sample."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path

    def cpu_fields(self) -> dict[str, float]:
        """Cumulative CPU seconds per state. Only deltas are used, so seconds
        replace the raw jiffies the kernel exposes without changing any result."""
        times = psutil.cpu_times()
        return {field: float(getattr(times, field, 0.0)) for field in CPU_STAT_FIELDS}

    def proc_cpu_seconds(self, pid: int) -> float:
        """The CPU seconds a process used."""
        times = psutil.Process(pid).cpu_times()
        return times.user + times.system

    def net_fields(self) -> dict[str, int]:
        """The network counters, without loopback and virtual interfaces."""
        counters = psutil.net_io_counters(pernic=True)
        interfaces = [counter for name, counter in counters.items() if name not in NET_IFACE_EXCLUDE]
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
        """The process state."""
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
        """The bytes a process read and wrote."""
        process = psutil.Process(pid)
        if not hasattr(process, "io_counters"):
            return 0, 0
        try:
            counters = process.io_counters()
        except psutil.AccessDenied:
            return 0, 0
        return counters.read_bytes, counters.write_bytes

    def open_fds(self, pid: int) -> int:
        """The open file count, or -1 when it cannot be read."""
        try:
            return psutil.Process(pid).num_fds()
        except psutil.AccessDenied:
            return -1

    def load_average(self) -> tuple[float, float, float]:
        """The load averages."""
        one, five, fifteen = psutil.getloadavg()
        return one, five, fifteen

    def memory_usage(self) -> dict:
        """The memory and swap usage."""
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
        """The usage of the disk that holds the storage path."""
        return {"disk": self.disk_usage(self.storage_path)}


class HostSampler:
    """One system metrics record of this host. CPU and I/O rates cover the window from `sample()` to
    `compute()`, which must be `CPU_SAMPLE_INTERVAL` long."""

    def __init__(self, storage_path: Path):
        self.reader = ProcMetricsReader(storage_path)
        self.cpu_percent = 0.0
        self.cpu_breakdown: dict[str, float] = {}
        # CPU seconds of every core in the window, which a process's share is measured against.
        self.cpu_seconds = 0.0
        self.network: dict[str, float] = {}
        self.disk_io: dict[str, float] = {}
        self._before: tuple[dict[str, float], dict[str, int], dict[str, int]] | None = None

    def sample(self) -> None:
        """Read the counters at the start of the window."""
        self._before = (self.reader.cpu_fields(), self.reader.net_fields(), self.reader.disk_io_fields())

    def compute(self) -> None:
        """Compute the CPU use and the network and disk rates over the window."""
        assert self._before is not None, "sample() must run before compute()"
        cpu_before, net_before, disk_before = self._before
        cpu_after = self.reader.cpu_fields()
        delta = {key: cpu_after[key] - cpu_before[key] for key in cpu_after}
        self.cpu_seconds = sum(delta.values())
        self.cpu_breakdown = self.get_cpu_breakdown(delta)
        self.cpu_percent = round(100 - self.cpu_breakdown["idle"], 2)

        net_after, disk_after = self.reader.net_fields(), self.reader.disk_io_fields()
        self.network = {
            "rx_bytes_per_sec": self.get_rate(net_before, net_after, "rx_bytes"),
            "tx_bytes_per_sec": self.get_rate(net_before, net_after, "tx_bytes"),
        }
        self.disk_io = {
            "read_bytes_per_sec": self.get_rate(disk_before, disk_after, "read_bytes"),
            "write_bytes_per_sec": self.get_rate(disk_before, disk_after, "write_bytes"),
        }

    @staticmethod
    def get_cpu_breakdown(delta: dict[str, float]) -> dict[str, float]:
        """The percent of CPU time in each state; all idle when no time passed."""
        total = sum(delta.values())
        if total <= 0:
            return {"user": 0.0, "system": 0.0, "iowait": 0.0, "irq": 0.0, "other": 0.0, "idle": 100.0}
        percent = lambda ticks: round(ticks / total * 100, 2)  # noqa: E731
        return {
            "user": percent(delta["user"] + delta["nice"]),
            "system": percent(delta["system"]),
            "iowait": percent(delta["iowait"]),
            "irq": percent(delta["irq"] + delta["softirq"]),
            "other": percent(delta["steal"]),
            "idle": percent(delta["idle"]),
        }

    @staticmethod
    def get_rate(before: dict[str, int], after: dict[str, int], key: str) -> float:
        """Bytes per second of a counter over the window."""
        return round((after[key] - before[key]) / CPU_SAMPLE_INTERVAL, 2)

    def get_record(self) -> dict:
        """The system metrics record, with the rates of the last window."""
        return {
            "time": datetime.now(UTC).isoformat(),
            "load_avg": self.reader.load_average(),
            "cpu_percent": self.cpu_percent,
            "cpu_breakdown": self.cpu_breakdown,
            "memory": self.reader.memory_usage(),
            "storage": self.reader.storage_usage(),
            "network": self.network,
            "disk_io": self.disk_io,
        }

    def get_sampled_record(self) -> dict:
        """The system metrics record over a window of its own, which this call waits out."""
        self.sample()
        time.sleep(CPU_SAMPLE_INTERVAL)
        self.compute()
        return self.get_record()
