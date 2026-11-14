from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import ClassVar

import psutil

from pilot.config.telemetry import TelemetryConfig

try:
    from datum_client import Batch, Datum

    HAS_DATUM_CLIENT = True
except ImportError:
    # Only the admin venv has it; the stdlib-only CLI does not. The logs are written regardless.
    HAS_DATUM_CLIENT = False


class MetricShipper:
    """Turns one collection tick's records into Datum samples and posts them once.

    The JSON-Lines logs remain the Admin UI's source of truth. Datum gets the
    machine metrics of the telemetry contract, the same names that every machine
    sends, and the database's as `mariadb_*`. Only Pilot's own processes are `pilot_*`.
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

    def add_machine(self) -> None:
        """Add the machine metrics of the telemetry contract, read now. Counters are totals since boot."""
        if not self.is_enabled:
            return
        when = datetime.now(UTC)
        system = Batch("system")
        system.gauge("boot_time", psutil.boot_time(), "seconds", ts=when)
        system.gauge("cpu_count", psutil.cpu_count(), ts=when)
        for cpu, times in enumerate(psutil.cpu_times(percpu=True)):
            for mode, seconds in times._asdict().items():
                system.counter("cpu", seconds, "seconds", ts=when, cpu=cpu, mode=mode)
        for window, value in zip(("1m", "5m", "15m"), psutil.getloadavg(), strict=True):
            system.gauge("load_average", value, ts=when, window=window)
        memory = psutil.virtual_memory()
        system.gauge("memory_total", memory.total, "bytes", ts=when)
        system.gauge("memory_available", memory.available, "bytes", ts=when)
        system.gauge("memory_swap_used", psutil.swap_memory().used, "bytes", ts=when)
        self.batches += [system, self._filesystems(when), self._devices(when)]

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
        batch.counter("buffer_pool_read_requests", record.get("Innodb_buffer_pool_read_requests", 0), ts=when)
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

    def add_backups(self, histories: list[dict[str, list[tuple[datetime, dict]]]]) -> None:
        """Add the number of sites whose newest backup of the last day succeeded or failed, over every bench.

        Pilot tells the owner of one site whose backups keep failing. Beacon reads the count, so it sees
        when backups fail on many servers at once, such as when object storage refuses uploads."""
        if not self.is_enabled:
            return
        newest = Counter(runs[0][1]["status"] for history in histories for runs in history.values() if runs)
        batch = Batch("pilot")
        for status in BACKUP_STATUSES:
            batch.gauge("site_backups", newest[status], status=status)
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
    def _filesystems(when: datetime) -> Batch:
        """The size and free space of each mounted disk. `available` is what a user other than root can use."""
        batch = Batch("system", "filesystem")
        for partition in psutil.disk_partitions():
            if partition.fstype in VIRTUAL_FILESYSTEMS or partition.mountpoint.startswith(SYSTEM_MOUNTS):
                continue
            usage = psutil.disk_usage(partition.mountpoint)
            labels = {
                "mountpoint": partition.mountpoint,
                "device": partition.device,
                "type": partition.fstype,
            }
            batch.gauge("size", usage.total, "bytes", ts=when, **labels)
            batch.gauge("available", usage.free, "bytes", ts=when, **labels)
        return batch

    @staticmethod
    def _devices(when: datetime) -> Batch:
        """The byte and error counters of each disk and network interface."""
        batch = Batch("system")
        for device, disk in psutil.disk_io_counters(perdisk=True).items():
            if not device.startswith(("loop", "ram")):
                batch.counter("disk_read", disk.read_bytes, "bytes", ts=when, device=device)
                batch.counter("disk_written", disk.write_bytes, "bytes", ts=when, device=device)
        for interface, network in psutil.net_io_counters(pernic=True).items():
            if interface == "lo" or interface.startswith(("veth", "docker")):
                continue
            batch.counter("network_receive", network.bytes_recv, "bytes", ts=when, interface=interface)
            batch.counter("network_transmit", network.bytes_sent, "bytes", ts=when, interface=interface)
            batch.counter("network_receive_errors", network.errin, ts=when, interface=interface)
            batch.counter("network_transmit_errors", network.errout, ts=when, interface=interface)
        return batch


def _read_time(record: dict) -> datetime | None:
    """Producer time from the record, so every sample in a tick lines up."""
    try:
        return datetime.fromisoformat(record["time"])
    except (KeyError, TypeError, ValueError):
        return None


SITE_STATES = ("up", "down", "maintenance")
# The same file systems that the Datum agent leaves out, so every machine sends the same set.
VIRTUAL_FILESYSTEMS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "overlay",
        "proc",
        "pstore",
        "ramfs",
        "rpc_pipefs",
        "securityfs",
        "selinuxfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
SYSTEM_MOUNTS = ("/proc/", "/sys/", "/dev/", "/run/", "/snap/")
BACKUP_STATUSES = ("success", "failed")


def site_state(result: dict) -> str:
    """The state of one site in one uptime ping result."""
    if result.get("maintenance"):
        return "maintenance"
    return "up" if result["up"] else "down"
