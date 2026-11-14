from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import ClassVar

from pilot.config.telemetry import TelemetryConfig

try:
    from datum_client import Batch, Datum

    HAS_DATUM_CLIENT = True
except ImportError:
    # Only the admin venv has it; the stdlib-only CLI does not. The logs are written regardless.
    HAS_DATUM_CLIENT = False



class MetricShipper:
    """Turns one collection tick's records into Datum samples and posts them once.

    The records stay exactly what the JSON-Lines logs hold - the logs remain the
    Admin UI's source of truth, and Datum gets the same numbers as a time series.
    Names follow the fact, not the shipper: host metrics are `system_*` and the
    database's are `mariadb_*`, so another agent reporting the same fact lands on
    the same series. Only Pilot's own processes are `pilot_*`.
    Without the datum_client package, or without an endpoint and token, every
    add_* call is a no-op.
    """

    # Raw MariaDB counter -> the label that separates it from its siblings.
    query_kinds: ClassVar[dict[str, str]] = {
        "Com_insert": "insert",
        "Com_update": "update",
        "Com_delete": "delete",
        "Com_select": "select",
    }

    def __init__(self, config: TelemetryConfig):
        self.config = config
        ships = HAS_DATUM_CLIENT and config.is_shipping_metrics
        self.client = Datum(config.endpoint, config.token) if ships else None
        self.batches: list[Batch] = []

    @property
    def is_enabled(self) -> bool:
        """Whether metrics are shipped."""
        return self.client is not None

    def add_system(self, record: dict) -> None:
        """Add the CPU, memory and storage metrics."""
        if not self.is_enabled:
            return
        when = _read_time(record)
        cpu = Batch("system", "cpu")
        # The total is 100 - idle, so it duplicates the breakdown. It stays because
        # it is the number every dashboard wants, and it keeps its own name so a
        # sum() over the modes can never quietly count the same CPU twice.
        cpu.gauge("usage", record.get("cpu_percent", 0.0), "percent", ts=when)
        for mode, percent in (record.get("cpu_breakdown") or {}).items():
            cpu.gauge("mode", percent, "percent", ts=when, mode=mode)

        load = Batch("system", "load")
        for window, value in zip(("1m", "5m", "15m"), record.get("load_avg") or [], strict=False):
            load.gauge("average", value, ts=when, window=window)

        self.batches += [cpu, load, self._memory(record, when), *self._storage(record, when)]

    def add_application(self, record: dict) -> None:
        """Add the process metrics."""
        if not self.is_enabled:
            return
        when = _read_time(record)
        batch = Batch("pilot", "process", bench=record["bench"])
        for process in record.get("processes") or []:
            service = process["service"]
            batch.up("", not process.get("missing"), ts=when, service=service)
            if process.get("missing"):
                continue
            batch.gauge("cpu", process["cpu_percent"], "percent", ts=when, service=service)
            batch.gauge("memory_rss", process["memory_rss_bytes"], "bytes", ts=when, service=service)
            batch.counter("io_read", process["read_bytes"], "bytes", ts=when, service=service)
            batch.counter("io_write", process["write_bytes"], "bytes", ts=when, service=service)
            batch.gauge("state", 1, ts=when, service=service, state=process["state"])
            if process["open_fds"] >= 0:
                batch.gauge("open_fds", process["open_fds"], ts=when, service=service)
        self.batches.append(batch)

    def add_database(self, record: dict | None) -> None:
        """Add the database metrics."""
        if not self.is_enabled or record is None:
            return
        when = _read_time(record)
        batch = Batch("mariadb")
        for key, kind in self.query_kinds.items():
            batch.counter("queries", record.get(key, 0), ts=when, kind=kind)
        batch.counter("questions", record.get("Questions", 0), ts=when)
        batch.counter("buffer_pool_reads", record.get("Innodb_buffer_pool_reads", 0), ts=when)
        batch.counter(
            "buffer_pool_read_requests", record.get("Innodb_buffer_pool_read_requests", 0), ts=when
        )
        batch.counter("row_lock_waits", record.get("Innodb_row_lock_waits", 0), ts=when)
        # MariaDB reports Innodb_row_lock_time in milliseconds.
        batch.counter("row_lock_wait", record.get("Innodb_row_lock_time", 0) / 1000, "seconds", ts=when)
        batch.gauge("connections", record.get("Threads_connected", 0), ts=when)
        batch.gauge("max_connections", record.get("max_connections", 0), ts=when)
        # total_ram_bytes is not shipped: it is the host's memory, already sent as
        # system_memory_total_bytes. It stays in the log for the Admin UI's ratio.
        batch.gauge("buffer_pool_size", record.get("innodb_buffer_pool_size", 0), "bytes", ts=when)
        self.batches.append(batch)

    def add_sites(self, results: list[dict]) -> None:
        """Add the number of sites in each state from one round of uptime pings.

        The ping is /api/method/ping. Installed apps can still run request hooks on it, so a down
        site can be an app fault, not only a platform fault. Every state is sent, with zeroes, so a reader can tell "no site is down" from "no data"."""
        if not self.is_enabled:
            return
        states = Counter(site_state(result) for result in results)
        batch = Batch("pilot")
        for state in SITE_STATES:
            batch.gauge("sites", states[state], state=state)
        self.batches.append(batch)

    def send(self) -> int:
        """One tick, one POST. Returns the HTTP status, or 0 when nothing landed."""
        # Datum has a __len__ for its buffer, so an idle client is falsy.
        if self.client is None or not self.batches:
            return 0
        status = self.client.send(*self.batches)
        self.batches = []
        return status

    @staticmethod
    def _memory(record: dict, when: datetime | None) -> Batch:
        """The memory metrics batch."""
        memory = record.get("memory") or {}
        batch = Batch("system", "memory")
        for target in ("total", "used", "cached", "free"):
            batch.gauge(target, memory.get(f"{target}_bytes", 0), "bytes", ts=when)
        batch.gauge("swap_used", memory.get("swap_used_bytes", 0), "bytes", ts=when)
        batch.gauge("usage", memory.get("percent", 0.0), "percent", ts=when)
        return batch

    @staticmethod
    def _storage(record: dict, when: datetime | None) -> list[Batch]:
        """The disk metrics batches."""
        disk = (record.get("storage") or {}).get("disk") or {}
        disk_io = record.get("disk_io") or {}
        batch = Batch("system", "disk")
        for target in ("total", "used", "free"):
            batch.gauge(target, disk.get(f"{target}_bytes", 0), "bytes", ts=when)
        batch.gauge("usage", disk.get("percent", 0.0), "percent", ts=when)
        batch.gauge("read", disk_io.get("read_bytes_per_sec", 0.0), "bytes_per_second", ts=when)
        batch.gauge("write", disk_io.get("write_bytes_per_sec", 0.0), "bytes_per_second", ts=when)

        network = record.get("network") or {}
        interface = Batch("system", "network")
        interface.gauge("receive", network.get("rx_bytes_per_sec", 0.0), "bytes_per_second", ts=when)
        interface.gauge("transmit", network.get("tx_bytes_per_sec", 0.0), "bytes_per_second", ts=when)
        return [batch, interface]


def _read_time(record: dict) -> datetime | None:
    """Producer time from the record, so every sample in a tick lines up."""
    try:
        return datetime.fromisoformat(record["time"])
    except (KeyError, TypeError, ValueError):
        return None


SITE_STATES = ("up", "down", "maintenance")


def site_state(result: dict) -> str:
    """The state of one site in one uptime ping result."""
    if result.get("maintenance"):
        return "maintenance"
    return "up" if result["up"] else "down"
