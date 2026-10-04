"""Light-weight monitoring daemon for bench process and system metrics."""

from __future__ import annotations

import json
import time
import typing
from datetime import UTC, datetime
from pathlib import Path

import psutil

from pilot.core.alerts import ALERT_SUSTAINED_SECONDS, SustainedAlerts, notify
from pilot.core.notification.events import record_alert
from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.core.server.monitoring_datum import MetricShipper
from pilot.core.server.monitoring_proc import ProcMetricsReader
from pilot.core.server.monitoring_processes import ProcessResolver
from pilot.utils import cli_root, iter_sibling_benches

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# Gap between the two samples used to turn cumulative counters into a rate.
CPU_SAMPLE_INTERVAL = 1.0

ALERT_EVENT = "resource_limit_breached"

# Raw MariaDB status counters/gauges + variables logged per cycle; the provider
# turns consecutive samples into rates.
_DB_STATUS_KEYS = (
    "Com_insert",
    "Com_update",
    "Com_delete",
    "Com_select",
    "Questions",
    "Innodb_buffer_pool_reads",
    "Innodb_buffer_pool_read_requests",
    "Innodb_row_lock_time",
    "Innodb_row_lock_waits",
    "Threads_connected",
)
_DB_VARIABLE_KEYS = ("innodb_buffer_pool_size", "max_connections")


class Monitor:
    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._configurator = MonitorConfigurator(bench)
        self._proc_reader = ProcMetricsReader(bench.path)
        self._system_cpu: float = 0.0
        self._cpu_breakdown: dict[str, float] = {}
        self._proc_cpu: dict[int, float] = {}
        self._network: dict[str, float] = {}
        self._disk_io: dict[str, float] = {}
        self._targets: dict[str, int] | None = None
        self._cpu_before: tuple[dict[str, float], dict[int, float]] | None = None
        self._io_before: tuple[dict[str, int], dict[str, int]] | None = None

    def monitored_targets(self) -> dict[str, int]:
        if self._targets is None:
            self._targets = ProcessResolver(self.bench).resolve()
        return self._targets

    def sample_cpu(self) -> None:
        pids = [pid for pid in self.monitored_targets().values() if psutil.pid_exists(pid)]
        self._cpu_before = (self._cpu_fields(), {pid: self._proc_cpu_seconds(pid) for pid in pids})

    def compute_cpu(self) -> None:
        assert self._cpu_before is not None, "sample_cpu() must run before compute_cpu()"
        fields_before, proc_before = self._cpu_before
        fields_after = self._cpu_fields()
        delta = {key: fields_after[key] - fields_before[key] for key in fields_after}
        delta_total = sum(delta.values())

        if delta_total > 0:
            percent = lambda ticks: round(ticks / delta_total * 100, 2)  # noqa: E731
            self._cpu_breakdown = {
                "user": percent(delta["user"] + delta["nice"]),
                "system": percent(delta["system"]),
                "iowait": percent(delta["iowait"]),
                "irq": percent(delta["irq"] + delta["softirq"]),
                "other": percent(delta["steal"]),
                "idle": percent(delta["idle"]),
            }
        else:
            self._cpu_breakdown = {
                "user": 0.0,
                "system": 0.0,
                "iowait": 0.0,
                "irq": 0.0,
                "other": 0.0,
                "idle": 100.0,
            }
        self._system_cpu = round(100 - self._cpu_breakdown["idle"], 2)
        self._proc_cpu = {
            pid: self._proc_usage(before, pid, delta_total) for pid, before in proc_before.items()
        }

    def sample_io(self) -> None:
        self._io_before = (self._net_fields(), self._disk_io_fields())

    def compute_io(self) -> None:
        assert self._io_before is not None, "sample_io() must run before compute_io()"
        net_before, disk_before = self._io_before
        net_after, disk_after = self._net_fields(), self._disk_io_fields()
        self._network = {
            "rx_bytes_per_sec": round(
                (net_after["rx_bytes"] - net_before["rx_bytes"]) / CPU_SAMPLE_INTERVAL, 2
            ),
            "tx_bytes_per_sec": round(
                (net_after["tx_bytes"] - net_before["tx_bytes"]) / CPU_SAMPLE_INTERVAL, 2
            ),
        }
        self._disk_io = {
            "read_bytes_per_sec": round(
                (disk_after["read_bytes"] - disk_before["read_bytes"]) / CPU_SAMPLE_INTERVAL,
                2,
            ),
            "write_bytes_per_sec": round(
                (disk_after["write_bytes"] - disk_before["write_bytes"]) / CPU_SAMPLE_INTERVAL,
                2,
            ),
        }

    @property
    def log_path(self) -> Path:
        return self._configurator.log_path

    @property
    def system_log_path(self) -> Path:
        return self._configurator.system_log_path

    @property
    def db_log_path(self) -> Path:
        return self._configurator.db_log_path

    @property
    def slow_query_log_path(self) -> Path:
        return self._configurator.slow_query_log_path

    @property
    def alerts_path(self) -> Path:
        return self.system_log_path.with_suffix(".alerts")

    def _alert_payload(self, breached: list[str], system_record: dict) -> dict[str, typing.Any]:
        """Alert body for the webhook, mail, and Central sinks: event, message, context."""
        limits = self.bench.config.resource_limits
        readings = self._readings(system_record)
        crossed = [
            {"limit": name, "threshold": getattr(limits, name), "reading": readings[name]}
            for name in breached
        ]
        summary = ", ".join(f"{item['limit']} at {item['reading']}%" for item in crossed)
        return {
            "event": ALERT_EVENT,
            "message": f"{self.bench.config.name}: {summary}",
            "context": {
                "bench": self.bench.config.name,
                "time": system_record["time"],
                "sustained_seconds": ALERT_SUSTAINED_SECONDS,
                "breached_limits": crossed,
            },
        }

    def send_alert_if_required(self, system_record: dict) -> None:
        """Send system alerts if required based on the breach limits set"""
        alerts = SustainedAlerts(self.alerts_path)
        due = alerts.due(self._breached_limits(system_record))

        # Recording runs off `sustained`, not `due`: a sink accepting the alert retires
        # the condition from `due` for good, and must not retire an incident the bench
        # never managed to write down. Once written it is not written again.
        unrecorded = alerts.unrecorded(alerts.sustained())
        if unrecorded and record_alert(
            self.bench,
            self._alert_payload(unrecorded, system_record),
            category="Server",
            severity="Warning",
            title="Resource limit breached",
        ):
            alerts.mark_recorded(unrecorded)

        if not due:
            return

        if notify(self.bench, self._alert_payload(due, system_record)):
            alerts.mark_notified(due)

    @staticmethod
    def _readings(system_record: dict) -> dict[str, float]:
        return {
            "cpu_usage_limit": system_record["cpu_percent"],
            "memory_usage_limit": system_record["memory"]["percent"],
            "disk_space_limit": system_record["storage"]["disk"]["percent"],
        }

    def _breached_limits(self, system_record: dict) -> list[str]:
        limits = self.bench.config.resource_limits
        breached = []
        for name, reading in self._readings(system_record).items():
            limit = getattr(limits, name)
            if limit and reading > limit:
                breached.append(name)
        return breached

    def collect_system_metrics(self) -> dict:
        record = {
            "time": datetime.now(UTC).isoformat(),
            "load_avg": self._load_average(),
            "cpu_percent": self._system_cpu,
            "cpu_breakdown": self._cpu_breakdown,
            "memory": self._memory_usage(),
            "storage": self._storage_usage(),
            "network": self._network,
            "disk_io": self._disk_io,
        }
        self._append(self.system_log_path, record)
        self.send_alert_if_required(record)
        return record

    def collect_database_metrics(self) -> dict | None:
        """One raw MariaDB sample per host. Never crashes the daemon on a bad DB."""
        if self.bench.config.db_type != "mariadb":
            return None
        try:
            from pilot.core.database import make_database
            from pilot.core.database.engines import MariaDB

            database = make_database(self.bench.config)
            if not isinstance(database, MariaDB):
                return None
            status = database.get_global_status()
            variables = database.get_global_variables()
        except Exception:
            return None
        record: dict[str, typing.Any] = {"time": datetime.now(UTC).isoformat()}
        for key in _DB_STATUS_KEYS:
            record[key] = _to_int(status.get(key))
        for key in _DB_VARIABLE_KEYS:
            record[key] = _to_int(variables.get(key))
        record["total_ram_bytes"] = self._memory_usage().get("total_bytes")
        self._append(self.db_log_path, record)
        return record

    def collect_slow_queries(self) -> None:
        """Append new slow-log rows to the occurrence log. Skips quietly if the
        slow log is off or the DB is unreachable so the daemon never crashes."""
        if self.bench.config.db_type != "mariadb":
            return
        try:
            from pilot.core.database import make_database
            from pilot.core.database.engines import MariaDB
            from pilot.core.database.slow_queries import SlowQueryLog

            database = make_database(self.bench.config)
            if not isinstance(database, MariaDB) or not database.is_slow_log_enabled():
                return
            log = SlowQueryLog(self.slow_query_log_path)
            log.append(database.scan_slow_queries(since=log.watermark()))
        except Exception:
            return

    def collect_application_metrics(self) -> dict:
        processes = []
        for service, pid in self.monitored_targets().items():
            try:
                processes.append(self._process_metrics(service, pid))
            except psutil.NoSuchProcess:
                # Either never started, or exited between resolving the pid and reading it.
                processes.append({"service": service, "pid": pid, "missing": True})

        record = {
            "time": datetime.now(UTC).isoformat(),
            "bench": self.bench.config.name,
            "processes": processes,
        }
        self._append(self.log_path, record)
        return record

    def _proc_usage(self, seconds_before: float, pid: int, delta_total: float) -> float:
        try:
            delta = self._proc_cpu_seconds(pid) - seconds_before
        except psutil.NoSuchProcess:
            return 0.0
        return round(delta / delta_total * 100, 2) if delta_total > 0 else 0.0

    def _process_metrics(self, service: str, pid: int) -> dict:
        read_bytes, write_bytes = self._io_bytes(pid)
        return {
            "service": service,
            "pid": pid,
            "state": self._process_state(pid),
            "cpu_percent": self._cpu_percent(pid),
            "memory_rss_bytes": self._proc_memory_bytes(pid),
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "open_fds": self._open_fds(pid),
        }

    def _cpu_percent(self, pid: int) -> float:
        return self._proc_cpu.get(pid, 0.0)

    def _cpu_fields(self) -> dict[str, float]:
        return self._proc_reader.cpu_fields()

    def _proc_cpu_seconds(self, pid: int) -> float:
        return self._proc_reader.proc_cpu_seconds(pid)

    def _net_fields(self) -> dict[str, int]:
        return self._proc_reader.net_fields()

    def _disk_io_fields(self) -> dict[str, int]:
        return self._proc_reader.disk_io_fields()

    def _process_state(self, pid: int) -> str:
        return self._proc_reader.process_state(pid)

    def _proc_memory_bytes(self, pid: int) -> int:
        return self._proc_reader.proc_memory_bytes(pid)

    def _io_bytes(self, pid: int) -> tuple[int, int]:
        return self._proc_reader.io_bytes(pid)

    def _open_fds(self, pid: int) -> int:
        return self._proc_reader.open_fds(pid)

    def _load_average(self) -> tuple[float, float, float]:
        return self._proc_reader.load_average()

    def _memory_usage(self) -> dict:
        return self._proc_reader.memory_usage()

    def _disk_usage(self, path: Path) -> dict:
        return self._proc_reader.disk_usage(path)

    def _storage_usage(self) -> dict:
        return self._proc_reader.storage_usage()

    @staticmethod
    def _append(path: Path, record: dict) -> None:
        with path.open("a") as log_file:
            log_file.write(json.dumps(record) + "\n")


def _to_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _production_monitors() -> list[Monitor]:
    from pilot.core.bench import Bench

    sentinel = cli_root() / "benches" / ".monitor-placeholder"
    return [
        Monitor(bench=Bench(bench_config, bench_path))
        for bench_path, bench_config in iter_sibling_benches(sentinel)
        if bench_config.production.enabled
    ]


def main() -> None:
    monitors = _production_monitors()
    if not monitors:
        return

    for monitor in monitors:
        monitor.sample_cpu()
        monitor.sample_io()
    time.sleep(CPU_SAMPLE_INTERVAL)

    shipper = MetricShipper(monitors[0].bench.config.telemetry)
    for monitor in monitors:
        monitor.compute_cpu()
        monitor.compute_io()
        shipper.add_application(monitor.collect_application_metrics())

    # System/DB-wide metrics describe the shared host, not any one bench -
    # collect them exactly once per tick, not once per bench.
    host = monitors[0]
    shipper.add_system(host.collect_system_metrics())
    shipper.add_database(host.collect_database_metrics())
    host.collect_slow_queries()
    # One tick, one POST, after every log is on disk.
    shipper.send()


if __name__ == "__main__":
    main()
