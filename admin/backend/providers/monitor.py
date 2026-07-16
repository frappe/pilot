from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_SECONDS = {"30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "1w": 604800}
MAX_POINTS = 400
DISK_SERIES = "Root Disk"


class MonitorProvider:
    """Time-series for a window from the monitor logs: system metrics in a
    shared JSON-Lines log, application metrics in a per-bench one."""

    def __init__(self, bench_root: Path, window: str) -> None:
        self._bench_root = bench_root
        self._window = window if window in WINDOW_SECONDS else "1h"
        self._cutoff = datetime.now(timezone.utc) - timedelta(seconds=WINDOW_SECONDS[self._window])

    def get_history(self) -> dict:
        from pilot.config.monitor_config import MonitorConfig
        from pilot.config.toml_store import BenchTomlStore

        config = BenchTomlStore.for_bench(self._bench_root).read()
        app_log = config.monitor.log_path or MonitorConfig.default_log_path(config.name)
        return {
            "window": self._window,
            "window_seconds": WINDOW_SECONDS[self._window],
            # Absolute epoch ms so the browser windows correctly regardless of its timezone.
            "now": int(datetime.now(timezone.utc).timestamp() * 1000),
            "system": self.get_system_metrics(config.monitor.system_log_path),
            "application": self.get_application_metrics(app_log, config.name),
        }

    def get_system_metrics(self, path: Path) -> dict:
        rows = self.get_records_in_window(path)
        return {
            "earliest": self.get_earliest(path),
            "points": [self.build_system_point(when, metrics) for when, metrics in rows],
            "storage": self.get_latest_storage(rows),
            "memory_total_mb": self.get_latest_memory_total(rows),
        }

    @staticmethod
    def get_latest_memory_total(rows: list) -> float | None:
        for _, metrics in reversed(rows):
            total = (metrics.get("memory") or {}).get("total_mb")
            if total is not None:
                return total
        return None

    def build_system_point(self, when: datetime, metrics: dict) -> dict:
        storage = metrics.get("storage") or {}
        disk = storage.get("disk")
        load = metrics["load_avg"]
        cpu = metrics.get("cpu_breakdown") or {}
        memory = metrics.get("memory") or {}
        network = metrics.get("network") or {}
        disk_io = metrics.get("disk_io") or {}
        point = {
            "time": self.to_epoch_ms(when),
            "Busy User": cpu.get("user"),
            "Busy System": cpu.get("system"),
            "Busy IOWait": cpu.get("iowait"),
            "Busy IRQ": cpu.get("irq"),
            "Busy Other": cpu.get("other"),
            "Used": memory.get("used_mb"),
            "Cached + Buffers": memory.get("cached_mb"),
            "Free": memory.get("free_mb"),
            "Swap Used": memory.get("swap_used_mb"),
            "Load1": load[0],
            "Load5": load[1],
            "Load15": load[2],
            DISK_SERIES: disk["percent"] if disk else None,
            "Received": network.get("rx_bytes_per_sec"),
            "Sent": network.get("tx_bytes_per_sec"),
            "Read": disk_io.get("read_bytes_per_sec"),
            "Write": disk_io.get("write_bytes_per_sec"),
        }
        return point

    @staticmethod
    def get_latest_storage(rows: list) -> dict | None:
        for _, metrics in reversed(rows):
            disk = (metrics.get("storage") or {}).get("disk")
            if disk:
                return {"disk": {"used_mb": disk.get("used_mb"), "total_mb": disk.get("total_mb"), "percent": disk.get("percent")}}
        return None

    def get_application_metrics(self, path: Path, bench_name: str) -> dict:
        rows = self.get_records_in_window(path)
        return {
            "earliest": self.get_earliest(path),
            "services": self.get_service_names(rows, bench_name),
            "cpu": [self.build_service_row(when, metrics, bench_name, "cpu_percent") for when, metrics in rows],
            "memory": [self.build_service_row(when, metrics, bench_name, "memory_rss_mb") for when, metrics in rows],
        }

    def get_records_in_window(self, path: Path) -> list[tuple[datetime, dict]]:
        # Records are appended in time order, so read newest-first and stop at
        # the first one older than the window, never past the cutoff.
        rows = []
        for record in self._get_records_reversed(path):
            when = self._get_time(record["time"])
            if when < self._cutoff:
                break
            rows.append((when, record))
        rows.reverse()

        if len(rows) <= MAX_POINTS:
            return rows
        step = len(rows) // MAX_POINTS + 1
        return rows[::step]

    @staticmethod
    def _get_time(value: str) -> datetime:
        """Older lines carry naive server-local time; astimezone() normalizes it to UTC."""
        when = datetime.fromisoformat(value)
        return when if when.tzinfo else when.astimezone(timezone.utc)

    def get_service_names(self, rows: list, bench_name: str) -> list[str]:
        names: list[str] = []
        for _, metrics in rows:
            for process in metrics.get("processes", []):
                short = self.get_short_name(process["service"], bench_name)
                if short not in names:
                    names.append(short)
        return sorted(names)

    def build_service_row(self, when: datetime, metrics: dict, bench_name: str, key: str) -> dict:
        row = {"time": self.to_epoch_ms(when)}
        for process in metrics.get("processes", []):
            if not process.get("missing"):
                row[self.get_short_name(process["service"], bench_name)] = process.get(key, 0)
        return row

    @staticmethod
    def get_short_name(service: str, bench_name: str) -> str:
        return service.removeprefix(f"{bench_name}-").removesuffix(".service")

    @staticmethod
    def _get_records_reversed(path: Path, block_size: int = 65536):
        """Yields records newest-first, in blocks from the end, so a short window
        never touches the whole file."""
        if not path.exists():
            return
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position > 0:
                size = min(block_size, position)
                position -= size
                handle.seek(position)
                lines = (handle.read(size) + remainder).split(b"\n")
                remainder = lines[0]  # first piece may be incomplete; carry it back
                for line in reversed(lines[1:]):
                    if line:
                        yield json.loads(line)
            if remainder:
                yield json.loads(remainder)

    @classmethod
    def get_earliest(cls, path: Path) -> int | None:
        if not path.exists():
            return None
        with path.open() as handle:
            first = handle.readline()
        return cls.to_epoch_ms(cls._get_time(json.loads(first)["time"])) if first.strip() else None

    @staticmethod
    def to_epoch_ms(when: datetime) -> int:
        return int(when.timestamp() * 1000)
