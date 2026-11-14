from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pilot import __version__
from pilot.config.monitor import get_central_request_log, get_update_log, monitor_log_dir
from pilot.config.telemetry import TelemetryConfig

try:
    import psutil
    from datum_client import Batch, Datum

    HAS_DATUM_CLIENT = True
except ImportError:
    # Only the admin venv has them; the stdlib-only CLI does not. The logs are written regardless.
    HAS_DATUM_CLIENT = False


class TelemetryShipper:
    """Sends Datum only what Beacon reads from a Pilot server: the health of Pilot and its sites,
    backups, disk space, calls to Central and update results. Everything else stays on the server.

    Pilot tells the customer about the problems of one server through Central. Datum gets the
    numbers that show a problem across many servers. Without the datum_client package, or
    without an endpoint and token, every call is a no-op.
    """

    def __init__(self, config: TelemetryConfig):
        self.config = config
        ships = HAS_DATUM_CLIENT and config.is_enabled
        self.client = Datum(config.endpoint, config.token) if ships else None
        self.batches: list[Batch] = []

    @property
    def is_enabled(self) -> bool:
        """Whether telemetry is sent."""
        return self.client is not None

    def add_info(self) -> None:
        """Add `pilot_info` with the Pilot version. It is the heartbeat of Pilot."""
        if self.is_enabled:
            self.batches.append(Batch("pilot").info("", version=__version__))

    def add_database_up(self, is_up: bool) -> None:
        """Add whether the database of the server accepts connections."""
        if self.is_enabled:
            self.batches.append(Batch("pilot", "database").up("", is_up))

    def add_filesystems(self) -> None:
        """Add the size and free space of each disk. `available` is what a user other than root can use."""
        if not self.is_enabled:
            return
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
            batch.gauge("size", usage.total, "bytes", **labels)
            batch.gauge("available", usage.free, "bytes", **labels)
        self.batches.append(batch)

    def add_central_requests(self, now: datetime) -> None:
        """Add the calls to Central in the last minute, by method and result."""
        if not self.is_enabled:
            return
        since = (now - timedelta(minutes=1)).isoformat()
        counts: Counter[tuple[str, str]] = Counter()
        for record in get_central_request_log().read_newest_first():
            if record.get("time", "") <= since:
                break
            counts[(record["method"], record["result"])] += 1
        batch = Batch("pilot", "central")
        for (method, result), count in counts.items():
            batch.gauge("requests_last_minute", count, method=method, result=result)
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
        """Post the metrics as one request. Returns the HTTP status, or 0 when nothing landed."""
        # Datum has a __len__ for its buffer, so an idle client is falsy.
        if self.client is None or not self.batches:
            return 0
        status = self.client.send(*self.batches)
        self.batches = []
        return status

    def send_update_events(self) -> None:
        """Send the update records written since the last accepted request, oldest first.

        The time of the newest sent record is kept in `updates.sent`. On the first run, Pilot
        starts from now, so the history of a server is not sent at once."""
        if self.client is None:
            return
        cursor = monitor_log_dir() / "updates.sent"
        if not cursor.exists():
            cursor.write_text(datetime.now(UTC).isoformat())
            return
        sent_until = cursor.read_text().strip()
        records = []
        for record in get_update_log().read_newest_first():
            if record.get("time", "") <= sent_until:
                break
            records.append(record)
        # The oldest first, so the cursor never passes a record that was not sent.
        records = records[::-1][:MAX_UPDATE_EVENTS]
        if not records:
            return
        if self.client.send_logs([update_line(record) for record in records]) == 200:
            cursor.write_text(records[-1]["time"])


def update_line(record: dict) -> dict:
    """One Datum log line for one app of an update."""
    attributes = {
        "app": record["app"],
        "repository": repository_name(record.get("repository_url", "")) or record["app"],
        "from_version": record.get("from_version", ""),
        "to_version": record.get("to_version", ""),
        "status": record["status"],
        "phase": record.get("phase", ""),
        "site": record.get("site", ""),
    }
    message = record.get("message") or f"update {record['status']}"
    if excerpt := record.get("output_excerpt"):
        message = f"{message}\n{excerpt}"
    return {
        "ts": record["time"],
        "product": "pilot",
        "service": "update",
        "source": "event",
        "level": "error" if record["status"] == "failed" else "info",
        # Keep the end of the output: the error is at the end.
        "message": message[-MAX_MESSAGE:],
        "attributes": {name: value for name, value in attributes.items() if value},
    }


def repository_name(url: str) -> str:
    """`owner/repository` from a git remote URL, such as `frappe/erpnext`."""
    if not url:
        return ""
    if url.startswith("git@"):
        url = "ssh://" + url.replace(":", "/", 1)
    return Path(urlsplit(url).path).as_posix().strip("/").removesuffix(".git")


SITE_STATES = ("up", "down", "maintenance")
BACKUP_STATUSES = ("success", "failed")
MAX_UPDATE_EVENTS = 1000
MAX_MESSAGE = 8192
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


def site_state(result: dict) -> str:
    """The state of one site in one uptime ping result."""
    if result.get("maintenance"):
        return "maintenance"
    return "up" if result["up"] else "down"
