from dataclasses import dataclass, field


@dataclass
class WorkerGroup:
    """One worker group: spawn ``count`` workers listening to ``queues``."""

    queues: list[str]
    count: int


@dataclass
class WorkerConfig:
    groups: list[WorkerGroup] = field(
        default_factory=lambda: [
            WorkerGroup(queues=["default"], count=2),
            WorkerGroup(queues=["short"], count=1),
            WorkerGroup(queues=["long"], count=1),
        ]
    )
