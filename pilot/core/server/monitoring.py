"""Light-weight monitoring daemon for bench process and system metrics."""

from __future__ import annotations

import json
import threading
import time
import traceback
import typing
from datetime import UTC, datetime
from pathlib import Path

import psutil

from pilot.config.alert_limit import RESOURCE_LIMIT_FIELDS
from pilot.config.monitor import database_server_log_path
from pilot.core.agent.roles.database.client import DatabaseAgentClient
from pilot.core.bench.telemetry import DatumCredential
from pilot.core.notification.alert_log import FIRING, RESOLVED, AlertLog
from pilot.core.server import Server
from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.core.server.monitoring_datum import SEND_INTERVAL, TelemetryShipper
from pilot.core.server.monitoring_proc import CPU_SAMPLE_INTERVAL, HostSampler
from pilot.core.server.monitoring_processes import ProcessResolver
from pilot.core.site import uptime_monitoring
from pilot.core.site.activity_rollup import SiteActivityRollup
from pilot.core.site.hourly_rollup import UptimeRollup
from pilot.internal.jsonl_log import utc_now

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# Seconds from the end of one tick to the start of the next.
METRICS_INTERVAL = 10.0
UPTIME_INTERVAL = 15.0
ROLLUP_INTERVAL = 60.0
TELEMETRY_INTERVAL = SEND_INTERVAL.total_seconds()

ALERT_EVENT = "resource_limit_breached"


class Monitor:
    # Raw MariaDB status counters/gauges + variables logged per cycle; the provider
    # turns consecutive samples into rates.
    db_status_keys = (
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
    # Short CPU and memory spikes are normal, so a breach must last this long to fire, and the
    # usage must stay under the limit this long to resolve.
    alert_window_seconds = 300

    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._configurator = MonitorConfigurator(bench)
        self.host = HostSampler(bench.path)
        self._proc_reader = self.host.reader
        self._proc_cpu: dict[int, float] = {}
        self._targets: dict[str, int] | None = None
        self._proc_cpu_before: dict[int, float] | None = None

    def monitored_targets(self) -> dict[str, int]:
        """The monitored processes, resolved on first use."""
        if self._targets is None:
            self._targets = ProcessResolver(self.bench).resolve()
        return self._targets

    def sample(self) -> None:
        """Read the host and process counters at the start of the sample window."""
        pids = [pid for pid in self.monitored_targets().values() if psutil.pid_exists(pid)]
        self.host.sample()
        self._proc_cpu_before = {pid: self._proc_cpu_seconds(pid) for pid in pids}

    def compute(self) -> None:
        """Compute the host and process usage over the sample window."""
        assert self._proc_cpu_before is not None, "sample() must run before compute()"
        self.host.compute()
        self._proc_cpu = {
            pid: self._proc_usage(before, pid, self.host.cpu_seconds)
            for pid, before in self._proc_cpu_before.items()
        }

    @property
    def log_path(self) -> Path:
        """The bench metrics log."""
        return self._configurator.log_path

    @property
    def system_log_path(self) -> Path:
        """The system metrics log."""
        return self._configurator.system_log_path

    @property
    def db_log_path(self) -> Path:
        """The database metrics log."""
        return self._configurator.db_log_path

    @property
    def database_server_log_path(self) -> Path:
        """The system metrics log of a remote server's VM."""
        return database_server_log_path()

    @property
    def slow_query_log_path(self) -> Path:
        """The slow query log."""
        return self._configurator.slow_query_log_path

    @property
    def alerts_path(self) -> Path:
        """The file with the resource alert state."""
        return self.system_log_path.with_suffix(".alerts")

    def _alert_payload(self, breaches: list[dict]) -> dict[str, typing.Any]:
        """Alert body for the webhook, mail, and Central sinks: event, message, context."""
        crossed = [{key: breach[key] for key in ("limit", "threshold", "reading")} for breach in breaches]
        summary = ", ".join(f"{item['limit']} at {item['reading']}%" for item in crossed)
        return {
            "event": ALERT_EVENT,
            "title": "Resource limit breached",
            "category": "Server",
            "severity": "Warning",
            "action_route": "/insights/analytics",
            "message": f"{self.bench.config.name}: {summary}",
            "context": {
                "bench": self.bench.config.name,
                "time": max(breach["time"] for breach in breaches),
                "sustained_seconds": self.alert_window_seconds,
                "breached_limits": crossed,
            },
        }

    def send_alert_if_required(self, system_record: dict) -> None:
        """A breach that holds for the window fires one alert for all limits it crossed, which resolves once
        usage stays under the limit for the window. A limit turned off drops its alert without a resolution."""
        limits = self.bench.config.resource_limits
        enabled = [name for name in RESOURCE_LIMIT_FIELDS if getattr(limits, name)]
        breaches = self.get_breaches(system_record)
        alerts = AlertLog(self.alerts_path, self.alert_window_seconds)
        alerts.retain(enabled)
        alerts.update(active=breaches, clear=[name for name in enabled if name not in breaches])
        alerts.deliver(
            self.bench, FIRING, lambda names: self._alert_payload(alerts.get_details(names)), grouped=True
        )
        alerts.deliver(self.bench, RESOLVED, self.get_recovered_payload, grouped=True)

    def get_recovered_payload(self, names: list[str]) -> dict[str, typing.Any]:
        """The event that resolves a resource alert."""
        return {
            "event": "resource_limit_recovered",
            "title": "Resource use back under the limit",
            "category": "Server",
            "severity": "Info",
            "action_route": "/insights/analytics",
            "message": f"{self.bench.config.name}: {', '.join(names)} back under the limit",
            "context": {"bench": self.bench.config.name, "time": utc_now(), "recovered_limits": names},
        }

    @staticmethod
    def _readings(system_record: dict) -> dict[str, float]:
        """The readings to compare with the resource limits."""
        return {
            "cpu_usage_limit": system_record["cpu_percent"],
            "memory_usage_limit": system_record["memory"]["percent"],
            "disk_space_limit": system_record["storage"]["disk"]["percent"],
        }

    def get_breaches(self, system_record: dict) -> dict[str, dict]:
        """Each resource limit the readings exceed, with its threshold, reading and time."""
        limits = self.bench.config.resource_limits
        breaches = {}
        for name, reading in self._readings(system_record).items():
            limit = getattr(limits, name)
            if limit and reading > limit:
                breaches[name] = {
                    "limit": name,
                    "threshold": limit,
                    "reading": reading,
                    "time": system_record["time"],
                }
        return breaches

    def collect_system_metrics(self) -> dict:
        """The system metrics record."""
        record = self.host.get_record()
        self._append(self.system_log_path, record)
        self.send_alert_if_required(record)
        return record

    @property
    def is_database_remote(self) -> bool:
        """Whether MariaDB runs on a database VM of its own."""
        return self.bench.config.db_type == "mariadb" and self.bench.config.mariadb.remote

    def collect_database_server_metrics(self) -> dict | None:
        """The system metrics record of a remote server's VM, from its Pilot agent. Skips quietly when
        the agent cannot be reached or is too old to know the request, so the daemon never stalls on it."""
        if not self.is_database_remote:
            return None
        try:
            record = DatabaseAgentClient.for_config(self.bench.config.mariadb).agent.get_host_metrics()
        except Exception:
            return None
        self._append(self.database_server_log_path, record)
        return record

    def get_database_memory_bytes(self, database_server_record: dict | None) -> int | None:
        """The memory of the host MariaDB runs on: a remote server's VM, or None when it did not answer."""
        if self.is_database_remote:
            return database_server_record["memory"]["total_bytes"] if database_server_record else None
        return self._proc_reader.memory_usage()["total_bytes"]

    def collect_database_metrics(self, database_server_record: dict | None = None) -> dict | None:
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
        for key in self.db_status_keys:
            record[key] = _to_int(status.get(key))
        for key in ("innodb_buffer_pool_size", "max_connections"):
            record[key] = _to_int(variables.get(key))
        record["total_ram_bytes"] = self.get_database_memory_bytes(database_server_record)
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
            log.append(database.get_new_slow_queries(since=log.watermark()))
        except Exception:
            return

    def collect_application_metrics(self) -> dict:
        """The process metrics record."""
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
        """The CPU percent a process used in the window."""
        try:
            delta = self._proc_cpu_seconds(pid) - seconds_before
        except psutil.NoSuchProcess:
            return 0.0
        return round(delta / delta_total * 100, 2) if delta_total > 0 else 0.0

    def _process_metrics(self, service: str, pid: int) -> dict:
        """The metrics of one process."""
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
        """The computed CPU percent of a process."""
        return self._proc_cpu.get(pid, 0.0)

    def _proc_cpu_seconds(self, pid: int) -> float:
        """The CPU seconds a process used."""
        return self._proc_reader.proc_cpu_seconds(pid)

    def _process_state(self, pid: int) -> str:
        """The process state."""
        return self._proc_reader.process_state(pid)

    def _proc_memory_bytes(self, pid: int) -> int:
        """The memory a process uses."""
        return self._proc_reader.proc_memory_bytes(pid)

    def _io_bytes(self, pid: int) -> tuple[int, int]:
        """The bytes a process read and wrote."""
        return self._proc_reader.io_bytes(pid)

    def _open_fds(self, pid: int) -> int:
        """The open file count of a process."""
        return self._proc_reader.open_fds(pid)

    @staticmethod
    def _append(path: Path, record: dict) -> None:
        """Append a JSON record to a log."""
        with path.open("a") as log_file:
            log_file.write(json.dumps(record) + "\n")


def _to_int(value: object) -> int:
    """The value as an int, or 0."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _production_monitors() -> list[Monitor]:
    """A monitor for each production bench."""
    from pilot.core.bench import Bench

    return [
        Monitor(bench=Bench(bench_config, bench_path))
        for bench_path, bench_config in Server().benches.iter_configs()
        if bench_config.production.enabled
    ]


def collect_metrics() -> None:
    """One metrics tick across every production bench."""
    monitors = _production_monitors()
    if not monitors:
        return

    for monitor in monitors:
        monitor.sample()
    time.sleep(CPU_SAMPLE_INTERVAL)

    for monitor in monitors:
        monitor.compute()
        monitor.collect_application_metrics()

    # System/DB-wide metrics describe the shared host, not any one bench -
    # collect them exactly once per tick, not once per bench.
    host = monitors[0]
    host.collect_system_metrics()
    host.collect_database_metrics(host.collect_database_server_metrics())
    host.collect_slow_queries()


def send_telemetry() -> None:
    """Send Datum what Beacon reads from this server, each 5 minutes. The uptime thread sends `pilot_sites`."""
    from pilot.core.bench.runtime import BenchRuntime

    monitors = _production_monitors()
    if not monitors:
        return

    bench = monitors[0].bench
    if bench.config.central.enabled:
        DatumCredential(bench, bench.config.telemetry.token, on_progress=print).rotate_if_expiring()

    shipper = TelemetryShipper(bench.config.telemetry)
    shipper.add_info()
    shipper.add_database_up(BenchRuntime(bench).is_database_ready)
    shipper.add_filesystems()
    now = datetime.now(UTC)
    shipper.add_backups([monitor.bench.backups.get_recent_history(now) for monitor in monitors])
    shipper.add_central_requests(now)
    shipper.send()
    shipper.send_update_events()


def run_every(interval: float, job: typing.Callable[[], None]) -> None:
    """Run job, then wait interval, forever. A failed tick is logged and the next one runs."""
    while True:
        try:
            job()
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def update_site_rollups() -> None:
    """Hourly per-site counts, so the Sites list reads a few KB instead of a week of logs."""
    from pilot.core.bench import Bench

    for bench_path, config in Server().benches.iter_configs():
        bench = Bench(config, bench_path)
        for rollup in (UptimeRollup(bench), SiteActivityRollup(bench)):
            try:
                rollup.update()
            except Exception:
                traceback.print_exc()


def main() -> None:
    """Uptime pings and telemetry get their own threads, so a slow metrics tick cannot delay them."""
    threading.Thread(
        target=run_every,
        args=(UPTIME_INTERVAL, uptime_monitoring.UptimeRunner().run),
        name="uptime",
        daemon=True,
    ).start()
    threading.Thread(
        target=run_every, args=(ROLLUP_INTERVAL, update_site_rollups), name="rollups", daemon=True
    ).start()
    threading.Thread(
        target=run_every, args=(TELEMETRY_INTERVAL, send_telemetry), name="telemetry", daemon=True
    ).start()
    run_every(METRICS_INTERVAL, collect_metrics)


if __name__ == "__main__":
    main()
