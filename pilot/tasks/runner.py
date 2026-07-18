from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pilot.tasks.callbacks import TaskCallbacks


@dataclass(frozen=True)
class TaskSubmission:
    task_id: str
    created: bool


class TaskRunner:
    def __init__(self, bench_root: Path) -> None:
        from pilot.internal.tasks.runner import runner_class

        self.__engine = runner_class()(bench_root)

    def run(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
        resource_key: str | None = None,
    ) -> str:
        return self.__engine.run(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
        )

    def submit(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
        resource_key: str | None = None,
    ) -> TaskSubmission:
        result = self.__engine.submit(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
        )
        return TaskSubmission(result.task_id, result.created)

    def kill(self, task_id: str) -> None:
        self.__engine.kill(task_id)
