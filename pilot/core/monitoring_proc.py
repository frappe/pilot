from __future__ import annotations

import os
import re
from pathlib import Path

CPU_STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
NET_IFACE_EXCLUDE = {"lo"}
DISK_DEVICE_PATTERN = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+)$")
SECTOR_BYTES = 512


class ProcMetricsReader:
    def __init__(self, bench_path: Path):
        self.bench_path = bench_path

    def cpu_fields(self) -> dict[str, int]:
        values = [
            int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        ]
        return dict(zip(CPU_STAT_FIELDS, values, strict=False))

    def proc_ticks(self, pid: int) -> int:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[13]) + int(fields[14])

    def net_fields(self) -> dict[str, int]:
        rx_bytes = tx_bytes = 0
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            iface, rest = line.split(":", 1)
            if iface.strip() in NET_IFACE_EXCLUDE:
                continue
            values = rest.split()
            rx_bytes += int(values[0])
            tx_bytes += int(values[8])
        return {"rx_bytes": rx_bytes, "tx_bytes": tx_bytes}

    def disk_io_fields(self) -> dict[str, int]:
        read_sectors = write_sectors = 0
        for line in Path("/proc/diskstats").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10 and DISK_DEVICE_PATTERN.match(fields[2]):
                read_sectors += int(fields[5])
                write_sectors += int(fields[9])
        return {
            "read_bytes": read_sectors * SECTOR_BYTES,
            "write_bytes": write_sectors * SECTOR_BYTES,
        }

    def read_status(self, pid: int) -> dict[str, str]:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        return {
            key: value.strip()
            for key, value in (line.split(":", 1) for line in lines if ":" in line)
        }

    def read_pss(self, pid: int) -> int:
        lines = Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
        for line in lines:
            if line.casefold().startswith("pss:"):
                parts = line.split(":", 1)[1].split()
                if parts:
                    return int(parts[0])
        return 0

    def io_bytes(self, pid: int) -> tuple[int, int]:
        lines = Path(f"/proc/{pid}/io").read_text().splitlines()
        data = {
            key: int(value)
            for key, value in (line.split(": ", 1) for line in lines if ": " in line)
        }
        return data.get("read_bytes", 0), data.get("write_bytes", 0)

    def open_fds(self, pid: int) -> int:
        try:
            return len(list(Path(f"/proc/{pid}/fd").iterdir()))
        except PermissionError:
            return -1

    def load_average(self) -> tuple[float, float, float]:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0]), float(parts[1]), float(parts[2])

    def memory_usage(self) -> dict:
        data = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = int(value.strip().split()[0])
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

    def disk_usage(self, path: Path) -> dict:
        stats = os.statvfs(path)
        total_bytes = stats.f_blocks * stats.f_frsize
        free_bytes = stats.f_bfree * stats.f_frsize
        used_bytes = total_bytes - free_bytes
        return {
            "total_mb": round(total_bytes / 1024**2, 2),
            "used_mb": round(used_bytes / 1024**2, 2),
            "free_mb": round(free_bytes / 1024**2, 2),
            "percent": round(used_bytes / total_bytes * 100, 2) if total_bytes else 0.0,
        }

    def storage_usage(self) -> dict:
        return {"disk": self.disk_usage(self.bench_path)}
