import re
from dataclasses import dataclass, field

from pilot.exceptions import ConfigError

_QUEUE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FRAPPE_QUEUES = ("default", "short", "long")


def parse_timeout(value):
    """A blank timeout is unset and a numeric string is seconds. Anything else is left for validate to reject."""
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
    return None if value in (None, "") else value


def frappe_default_timeout(queue: str) -> int:
    """The job timeout Frappe gives a queue that sets none."""
    return 1500 if queue == "long" else 300


@dataclass
class WorkerGroup:
    """One worker group: spawn ``count`` workers listening to ``queues``.
    ``timeout`` is the default job timeout of those queues, in seconds; None keeps Frappe's."""

    queues: list[str]
    count: int
    timeout: int | None = None

    def to_dict(self) -> dict:
        return {"queues": list(self.queues), "count": self.count, "timeout": self.timeout}

    @property
    def effective_timeout(self) -> int:
        """Its own timeout, else the longest Frappe default of its queues."""
        return self.timeout or max(frappe_default_timeout(queue) for queue in self.queues)


@dataclass
class WorkerConfig:
    groups: list[WorkerGroup] = field(
        default_factory=lambda: [
            WorkerGroup(queues=["default", "short", "long"], count=1),
        ]
    )

    @property
    def queues(self) -> list[str]:
        """Every queue across the groups, deduped, order preserved."""
        return list(dict.fromkeys(queue for group in self.groups for queue in group.queues))

    @property
    def queue_timeouts(self) -> dict[str, int]:
        """The job timeout of each queue that a group sets one for."""
        return {queue: group.timeout for group in self.groups if group.timeout for queue in group.queues}

    @property
    def count(self) -> int:
        """Workers across every group."""
        return sum(group.count for group in self.groups)

    def collapse(self) -> None:
        """Fold the groups into one pool: the union of the queues, the total count, the longest timeout.
        A blank timeout counts as Frappe's default, and stays blank when every group is blank."""
        timeout = None
        if any(group.timeout for group in self.groups):
            timeout = max(group.effective_timeout for group in self.groups)
        self.groups = [WorkerGroup(queues=self.queues, count=self.count, timeout=timeout)]

    @classmethod
    def from_dict(cls, data: list) -> "WorkerConfig":
        # [[workers]] array-of-tables: each group lists queues and a count.
        if not isinstance(data, list) or not data:
            return cls()
        groups = [
            WorkerGroup(
                queues=entry.get("queues", [entry.get("queue", "default")]),
                count=entry.get("count", 1),
                timeout=entry.get("timeout"),
            )
            for entry in data
        ]
        return cls(groups=groups)

    def validate(self) -> None:
        if not self.groups:
            raise ConfigError("workers.groups must contain at least one worker group.")
        for i, group in enumerate(self.groups):
            self.validate_group(f"workers[{i}]", group)
        self.validate_timeouts()

    @staticmethod
    def validate_group(prefix: str, group: WorkerGroup) -> None:
        if not isinstance(group.queues, list) or not group.queues:
            raise ConfigError(f"{prefix}.queues must be a non-empty list.")
        for queue in group.queues:
            # A queue name reaches a systemd ExecStart and a supervisor stanza, where
            # a newline would start a directive of the attacker's choosing.
            if not isinstance(queue, str) or not _QUEUE_NAME_PATTERN.match(queue):
                raise ConfigError(
                    f"{prefix}.queues entries must be 1-64 characters of letters, "
                    f"numbers, '-' or '_' (got {queue!r})."
                )
        if not isinstance(group.count, int) or group.count < 1:
            raise ConfigError(f"{prefix}.count must be a positive integer, got '{group.count}'.")
        if group.timeout is not None and (
            not isinstance(group.timeout, int) or isinstance(group.timeout, bool) or group.timeout < 1
        ):
            raise ConfigError(
                f"{prefix}.timeout must be a positive number of seconds, got '{group.timeout}'."
            )

    def validate_timeouts(self) -> None:
        """Frappe holds one timeout per queue, so two groups cannot give the same queue different ones."""
        timeouts: dict[str, int] = {}
        for group in self.groups:
            if group.timeout is None:
                continue
            for queue in group.queues:
                if timeouts.setdefault(queue, group.timeout) != group.timeout:
                    raise ConfigError(
                        f"Queue '{queue}' has two timeouts: {timeouts[queue]} and {group.timeout} seconds."
                    )
