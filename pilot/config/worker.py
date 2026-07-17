from dataclasses import dataclass, field

from pilot.exceptions import ConfigError


@dataclass
class WorkerGroup:
    """One worker group: spawn ``count`` workers listening to ``queues``."""

    queues: list[str]
    count: int


@dataclass
class WorkerConfig:
    groups: list[WorkerGroup] = field(
        default_factory=lambda: [
            WorkerGroup(queues=["default", "short", "long"], count=1),
        ]
    )

    @classmethod
    def from_dict(cls, data: list) -> "WorkerConfig":
        # [[workers]] array-of-tables: each group lists queues and a count.
        if not isinstance(data, list) or not data:
            return cls()
        groups = [
            WorkerGroup(
                queues=entry.get("queues", [entry.get("queue", "default")]),
                count=entry.get("count", 1),
            )
            for entry in data
        ]
        return cls(groups=groups)

    def validate(self) -> None:
        if not self.groups:
            raise ConfigError("workers.groups must contain at least one worker group.")
        for i, group in enumerate(self.groups):
            prefix = f"workers[{i}]"
            if not isinstance(group.queues, list) or not group.queues:
                raise ConfigError(f"{prefix}.queues must be a non-empty list.")
            if not all(isinstance(q, str) and q for q in group.queues):
                raise ConfigError(f"{prefix}.queues must contain non-empty strings.")
            if not isinstance(group.count, int) or group.count < 1:
                raise ConfigError(f"{prefix}.count must be a positive integer, got '{group.count}'.")
