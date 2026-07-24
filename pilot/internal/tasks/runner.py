from __future__ import annotations

import hashlib
import importlib
import logging
import pkgutil
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.exceptions import TaskNotRunningError
from pilot.internal.tasks.authoring import required_task_args
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.payload import TaskPayloadBuilder
from pilot.internal.tasks.process import TaskProcess
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker import task_workers
from pilot.tasks import Task
from pilot.tasks.callbacks import TaskCallback as TaskCallback
from pilot.tasks.callbacks import TaskCallbacks

TASK_RETENTION_LIMIT = 100
_TASK_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "tasks"
_NOT_A_TASK_MODULE = {"base"}
_REGISTRY: tuple[dict[str, type[Task]], dict[str, list[str]]] | None = None
_RUNNER_CLASS: type | None = None


def generate_task_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


@dataclass(frozen=True)
class SubmissionResult:
    task_id: str
    created: bool


class TaskRunner:
    """Generic task-submission engine bound by pilot.tasks.TaskRunner."""

    def __init__(
        self,
        bench_root: Path,
        jobs: dict[str, type[Task]],
        required_args: dict[str, list[str]],
    ) -> None:
        self._bench_root = bench_root
        self._store = TaskStore(bench_root)
        self._processes = TaskProcess(bench_root)
        self._payloads = TaskPayloadBuilder(
            bench_root,
            jobs,
            required_args,
            generate_task_id,
        )

    def run(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
    ) -> str:
        return self.submit(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        ).task_id

    def submit(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
    ) -> SubmissionResult:
        payload = self._payloads.build(command, args, callbacks)
        if idempotency_key is None:
            self._store.create_queued(
                payload.metadata,
                payload.private_files,
                resource_key=resource_key,
                resource_handoff_from=resource_handoff_from,
            )
            submission = SubmissionResult(payload.task_id, True)
        else:
            idempotency_digest = self._idempotency_digest(idempotency_key)
            creation = self._store.create_idempotent_queued(
                payload.metadata,
                payload.private_files,
                idempotency_digest,
                payload.request_fingerprint,
                resource_key=resource_key,
            )
            if not creation.created:
                return SubmissionResult(creation.task_id, False)
            submission = SubmissionResult(creation.task_id, True)
        self._run_post_submission_housekeeping()
        return submission

    def _run_post_submission_housekeeping(self) -> None:
        for operation in (
            lambda: task_workers.wake(self._bench_root),
            lambda: self._store.purge_terminal(TASK_RETENTION_LIMIT),
        ):
            try:
                operation()
            except Exception as exc:
                logging.debug("Post-submission housekeeping step failed: %s", exc)

    def kill(self, task_id: str) -> None:
        status = self._store.read_status(task_id)
        if not status.is_active:
            raise TaskNotRunningError(f"Task is not active: {task_id} (status={status.value})")

        if status == TaskStatus.QUEUED:
            if not self._store.transition(
                task_id,
                TaskStatus.QUEUED,
                TaskStatus.KILLED,
                {"finished_at": datetime.now(UTC).isoformat()},
            ):
                current = self._store.read_status(task_id)
                raise TaskNotRunningError(f"Task is not active: {task_id} (status={current.value})")
            self._store.remove_private_files(task_id, "secrets.json")
            try:
                task_workers.wake(self._bench_root)
            except Exception as exc:
                logging.debug("Failed to wake task workers after kill: %s", exc)
            return
        self._processes.cancel(task_id)

    @staticmethod
    def _idempotency_digest(key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 255:
            raise ValueError("Idempotency-Key must contain between 1 and 255 characters")
        return hashlib.sha256(key.encode()).hexdigest()


def runner_class() -> type:
    global _RUNNER_CLASS
    if _RUNNER_CLASS is not None:
        return _RUNNER_CLASS

    class BoundTaskRunner(TaskRunner):
        def __init__(self, bench_root: Path) -> None:
            jobs, required_args = task_registry()
            super().__init__(bench_root, jobs, required_args)

    _RUNNER_CLASS = BoundTaskRunner
    return _RUNNER_CLASS


def task_registry() -> tuple[dict[str, type[Task]], dict[str, list[str]]]:
    global _REGISTRY
    if _REGISTRY is None:
        tasks = discover_tasks()
        jobs = {cls.command: cls for cls in tasks}
        required_args = {cls.command: required_task_args(cls) for cls in tasks}
        _REGISTRY = (jobs, required_args)
    return _REGISTRY


def discover_tasks() -> list[type[Task]]:
    tasks = []
    for module_info in pkgutil.iter_modules([str(_TASK_PACKAGE_DIR)], prefix="pilot.tasks."):
        if module_info.name.rsplit(".", 1)[-1] in _NOT_A_TASK_MODULE:
            continue
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Task) and value.command:
                tasks.append(value)
    return tasks
