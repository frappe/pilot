from __future__ import annotations

from datetime import datetime
from pathlib import Path

from admin.backend.providers.windowed_log import WindowedLogProvider

MAX_POINTS = 400
DISK_SERIES = "Root Disk"


class SystemMonitoringProvider(WindowedLogProvider):
    """Time-series for a window from the monitor logs: system metrics in a
    shared JSON-Lines log, application metrics in a per-bench one."""

    def __init__(self, bench_root: Path, window: str) -> None:
        super().__init__(window)
        self._bench_root = bench_root

    def get_history(self) -> dict:
        from pilot.config import BenchConfig, MonitorConfig

        config = BenchConfig.read(self._bench_root)
        app_log = config.monitor.log_path or MonitorConfig.default_log_path(config.name)
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            # Absolute epoch ms so the browser windows correctly regardless of its timezone.
            "now": self.now_ms(),
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
        return {
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

    @staticmethod
    def get_latest_storage(rows: list) -> dict | None:
        for _, metrics in reversed(rows):
            disk = (metrics.get("storage") or {}).get("disk")
            if disk:
                return {
                    "disk": {
                        "used_mb": disk.get("used_mb"),
                        "total_mb": disk.get("total_mb"),
                        "percent": disk.get("percent"),
                    }
                }
        return None

    def get_application_metrics(self, path: Path, bench_name: str) -> dict:
        rows = self.get_records_in_window(path)
        return {
            "earliest": self.get_earliest(path),
            "services": self.get_service_names(rows, bench_name),
            "cpu": [
                self.build_service_row(when, metrics, bench_name, "cpu_percent") for when, metrics in rows
            ],
            "memory": [
                self.build_service_row(when, metrics, bench_name, "memory_rss_mb") for when, metrics in rows
            ],
        }

    def get_records_in_window(self, path: Path) -> list[tuple[datetime, dict]]:
        rows = [
            (when, record)
            for record in self.records_in_window(path)
            if (when := self.get_time(record["time"])) is not None
        ]
        rows.reverse()

        if len(rows) <= MAX_POINTS:
            return rows
        step = len(rows) // MAX_POINTS + 1
        return rows[::step]

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
