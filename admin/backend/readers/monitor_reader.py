from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_SECONDS = {"30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "1w": 604800}
MAX_POINTS = 400
DISK_SERIES = "Root Disk"


class MonitorHistoryReader:
    """Renders time-series from the monitor log files for a selected window.

    System metrics live in a shared log; application metrics in a per-bench log.
    Each file is JSON Lines: one record per line, each carrying a `time` field.
    """

    def __init__(self, bench_root: Path, window: str) -> None:
        self._bench_root = bench_root
        self._window = window if window in WINDOW_SECONDS else "1h"
        self._cutoff = datetime.now() - timedelta(seconds=WINDOW_SECONDS[self._window])

    def read(self) -> dict:
        from pilot.config.monitor_config import MonitorConfig
        from pilot.config.toml_store import BenchTomlStore

        config = BenchTomlStore.for_bench(self._bench_root).read()
        app_log = config.monitor.log_path or MonitorConfig.default_log_path(config.name)
        return {
            "window": self._window,
            "window_seconds": WINDOW_SECONDS[self._window],
            # Absolute epoch ms so the browser windows correctly regardless of its
            # timezone — log timestamps are naive (server-local).
            "now": int(datetime.now().timestamp() * 1000),
            "system": self._system(config.monitor.system_log_path),
            "application": self._application(app_log, config.name),
        }

    def _system(self, path: Path) -> dict:
        rows = self._read_window(path)
        return {
            "earliest": self._earliest(path),
            "points": [self._system_point(when, metrics) for when, metrics in rows],
            "storage": self._latest_storage(rows),
            "memory_total_mb": self._latest_memory_total(rows),
        }

    @staticmethod
    def _latest_memory_total(rows: list) -> float | None:
        for _, metrics in reversed(rows):
            total = (metrics.get("memory") or {}).get("total_mb")
            if total is not None:
                return total
        return None

    def _system_point(self, when: datetime, metrics: dict) -> dict:
        storage = metrics.get("storage") or {}
        disk = storage.get("disk")
        load = metrics["load_avg"]
        cpu = metrics.get("cpu_breakdown") or {}
        memory = metrics.get("memory") or {}
        network = metrics.get("network") or {}
        disk_io = metrics.get("disk_io") or {}
        point = {
            "time": self._ms(when),
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

    @classmethod
    def _latest_storage(cls, rows: list) -> dict | None:
        for _, metrics in reversed(rows):
            storage = metrics.get("storage")
            if storage and storage.get("disk"):
                return {"disk": cls._slim(storage["disk"])}
        return None

    @staticmethod
    def _slim(entry: dict) -> dict:
        return {"used_mb": entry.get("used_mb"), "total_mb": entry.get("total_mb"), "percent": entry.get("percent")}

    def _application(self, path: Path, bench_name: str) -> dict:
        rows = self._read_window(path)
        return {
            "earliest": self._earliest(path),
            "services": self._service_names(rows, bench_name),
            "cpu": [self._service_row(when, metrics, bench_name, "cpu_percent") for when, metrics in rows],
            "memory": [self._service_row(when, metrics, bench_name, "memory_rss_mb") for when, metrics in rows],
        }

    def _read_window(self, path: Path) -> list[tuple[datetime, dict]]:
        # Records are appended in time order, so read newest-first and stop at the
        # first one older than the window — we never read past the cutoff.
        rows = []
        for record in self._iter_records_reversed(path):
            when = datetime.fromisoformat(record["time"])
            if when < self._cutoff:
                break
            rows.append((when, record))
        rows.reverse()
        return self._downsample(rows)

    def _service_names(self, rows: list, bench_name: str) -> list[str]:
        names: list[str] = []
        for _, metrics in rows:
            for process in metrics.get("processes", []):
                short = self._short(process["service"], bench_name)
                if short not in names:
                    names.append(short)
        return sorted(names)

    def _service_row(self, when: datetime, metrics: dict, bench_name: str, key: str) -> dict:
        row = {"time": self._ms(when)}
        for process in metrics.get("processes", []):
            if not process.get("missing"):
                row[self._short(process["service"], bench_name)] = process.get(key, 0)
        return row

    @staticmethod
    def _short(service: str, bench_name: str) -> str:
        return service.removeprefix(f"{bench_name}-").removesuffix(".service")

    @staticmethod
    def _iter_records_reversed(path: Path, block_size: int = 65536):
        """Yield records newest-first, reading the file in blocks from the end so a
        short window never touches the whole file."""
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
    def _earliest(cls, path: Path) -> int | None:
        if not path.exists():
            return None
        with path.open() as handle:
            first = handle.readline()
        return cls._ms(datetime.fromisoformat(json.loads(first)["time"])) if first.strip() else None

    @staticmethod
    def _downsample(rows: list) -> list:
        if len(rows) <= MAX_POINTS:
            return rows
        step = len(rows) // MAX_POINTS + 1
        return rows[::step]

    @staticmethod
    def _ms(when: datetime) -> int:
        return int(when.timestamp() * 1000)


def latest_system_metrics(bench_root: Path) -> dict:
    """The monitoring daemon's most recent system sample for this bench (carrying
    cpu_percent, memory.percent, storage.disk.percent), or {} if it hasn't written
    one yet. Reads only the tail of the log, never the whole file."""
    from pilot.config.toml_store import BenchTomlStore

    config = BenchTomlStore.for_bench(bench_root).read()
    for record in MonitorHistoryReader._iter_records_reversed(config.monitor.system_log_path):
        return record
    return {}
