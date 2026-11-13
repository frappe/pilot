from __future__ import annotations

import functools
import hashlib
import importlib
import logging
import pkgutil
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.exceptions import TaskNotCancellableError, TaskNotRunningError
from pilot.internal.tasks.authoring import required_task_args
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.payload import TaskPayloadBuilder
from pilot.internal.tasks.process import TaskProcess
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker import task_workers
from pilot.managers.task.control import TaskWorkerControl
from pilot.tasks import Task
from pilot.tasks.callbacks import TaskCallback as TaskCallback
from pilot.tasks.callbacks import TaskCallbacks

TASK_RETENTION_LIMIT = 100


def generate_task_id() -> str:
    """A new task id: the local time and a random suffix."""
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
        """Submit the task and return its id."""
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
        """Queue the task, reusing an active task with the same idempotency key."""
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

    def find_idempotent_task(self, command: str, args: dict, key: str | None) -> str | None:
        """The active task queued with this idempotency key, if any."""
        if key is None:
            return None
        digest = self._idempotency_digest(key)
        payload = self._payloads.build(command, args, None)
        return self._store.find_idempotent_task(digest, payload.request_fingerprint)

    def _run_post_submission_housekeeping(self) -> None:
        """Wake the worker and purge old tasks; a failure here does not fail the submit."""
        for operation in (
            TaskWorkerControl(self._bench_root).wake,
            lambda: self._store.purge_terminal(TASK_RETENTION_LIMIT),
        ):
            try:
                operation()
            except Exception as exc:
                logging.debug("Post-submission housekeeping step failed: %s", exc)

    def kill(self, task_id: str) -> None:
        """Cancel a queued task or stop a running one."""
        status = self._store.read_status(task_id)
        if not status.is_active:
            raise TaskNotRunningError(f"Task is not active: {task_id} (status={status.value})")

        command = self._store.read_metadata(task_id).get("command", "")
        if status == TaskStatus.RUNNING and not is_command_cancellable_while_running(command):
            raise TaskNotCancellableError(f"'{command}' cannot be cancelled once it has started.")

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
        """SHA-256 of a valid idempotency key."""
        if not isinstance(key, str) or not key or len(key) > 255:
            raise ValueError("Idempotency-Key must contain between 1 and 255 characters")
        return hashlib.sha256(key.encode()).hexdigest()


@functools.cache
def runner_class() -> type:
    """The TaskRunner subclass bound to the discovered task registry."""

    class BoundTaskRunner(TaskRunner):
        def __init__(self, bench_root: Path) -> None:
            jobs, required_args = task_registry()
            super().__init__(bench_root, jobs, required_args)

    return BoundTaskRunner


@functools.cache
def task_registry() -> tuple[dict[str, type[Task]], dict[str, list[str]]]:
    """Every task class by command, with each command's required arguments."""
    tasks = discover_tasks()
    jobs = {cls.command: cls for cls in tasks}
    required_args = {cls.command: required_task_args(cls) for cls in tasks}
    return jobs, required_args


def is_command_cancellable_while_running(command: str) -> bool:
    """Killing some tasks mid-run leaves partial state behind, so they opt out.
    Queued tasks are always cancellable - none of their work has started."""
    jobs, _ = task_registry()
    task_class = jobs.get(command)
    return task_class.is_cancellable_while_running if task_class else True


def is_command_listed(command: str) -> bool:
    """Whether the command's task class shows up in listings. Unknown commands stay listed."""
    jobs, _ = task_registry()
    task_class = jobs.get(command)
    return task_class.is_listed if task_class else True


def discover_tasks() -> list[type[Task]]:
    """Every Task subclass in the pilot.tasks modules."""
    tasks = []
    for module_info in pkgutil.iter_modules(
        [str(Path(__file__).resolve().parents[2] / "tasks")], prefix="pilot.tasks."
    ):
        if module_info.name.rsplit(".", 1)[-1] in {"base"}:
            continue
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Task) and value.command:
                tasks.append(value)
    return tasks
