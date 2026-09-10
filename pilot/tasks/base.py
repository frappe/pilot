from __future__ import annotations

import functools
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pilot.commands import Arg

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.tasks.callbacks import TaskCallbacks


@dataclass(frozen=True)
class TaskSubmission:
    task_id: str
    created: bool


class _TaskStep:
    def __init__(self, task, key: str) -> None:
        self.task = task
        self.key = key

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None or (issubclass(exc_type, SystemExit) and exc_value.code in (0, None)):
            self.task.clear_step(self.key)
            return
        self.task.mark_step_failed(self.key)


def step(key: str, label: str | Callable[[Any], str] = ""):
    """Wrap a task method in a UI step."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            resolved_label = label(self) if callable(label) else label
            with self.step(key, resolved_label):
                return func(self, *args, **kwargs)

        return wrapper

    return decorator


@dataclass(kw_only=True)
class Task:
    """Dataclass task module discovered by pilot.tasks."""

    command: ClassVar[str] = ""
    has_done_step: ClassVar[bool] = True
    required_submit_args: ClassVar[tuple[str, ...]] = ()
    # Audit "who queued this" on queue. Turn off for frequent polling/read-only tasks.
    audit_on_queue: ClassVar[bool] = True
    # Turn off for tasks that leave partial state behind when killed mid-run.
    # Queued tasks can always be cancelled - nothing has run yet.
    is_cancellable_while_running: ClassVar[bool] = True
    # Turn off to keep a task out of listings; it stays readable by task id.
    is_listed: ClassVar[bool] = True
    # Non-sensitive queue args safe to record in the audit entry.
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("site", "app", "name", "repo", "branch", "marketplace_app")

    bench: "Bench"
    bench_root: Path

    @classmethod
    def queue(
        cls,
        bench: "Bench",
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
        **args,
    ) -> str:
        callbacks = cls._queue_callbacks(bench, args, callbacks)
        task_id = bench.tasks.run_task(
            cls,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
            **args,
        )
        cls._audit_queued(bench, task_id, args)
        return task_id

    @classmethod
    def _audit_queued(cls, bench: "Bench", task_id: str, args: dict) -> None:
        """Record who queued this task. Actor/IP are added by the audit context provider."""
        if not cls.audit_on_queue:
            return
        fields: dict[str, Any] = {"event": "queued", "command": cls.command, "task_id": task_id}
        safe = {key: args[key] for key in cls._AUDIT_ARG_KEYS if key in args}
        if safe:
            fields["args"] = safe
        if "site" in safe:
            fields["site"] = safe["site"]
        bench.audit_action("task", fields)

    @classmethod
    def queue_submission(
        cls,
        bench: "Bench",
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
        **args,
    ) -> TaskSubmission:
        callbacks = cls._queue_callbacks(bench, args, callbacks)
        return bench.tasks.submit_task(
            cls,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
            **args,
        )

    @classmethod
    def _queue_callbacks(
        cls,
        bench: "Bench",
        args: dict,
        explicit: "TaskCallbacks | None",
    ) -> "TaskCallbacks | None":
        from pilot.tasks.callbacks import task_callbacks_for

        constructor_args = cls._constructor_args(args)
        task = cls(bench=bench, bench_root=bench.path, **constructor_args)
        declared = task_callbacks_for(task)
        if not declared:
            return explicit
        if explicit:
            return {**declared, **explicit}
        return declared

    @classmethod
    def _constructor_args(cls, args: dict) -> dict:
        valid = {
            field.name for field in fields(cls) if field.init and field.name not in {"bench", "bench_root"}
        }
        return {key: value for key, value in args.items() if key in valid}

    def __post_init__(self) -> None:
        self._current_step: str | None = None
        self._failed_steps: set[str] = set()

    def step(self, key: str, label: str = "") -> AbstractContextManager[None]:
        self._current_step = key
        print(f"STEP {key},{time.time():.3f} {label}", flush=True)
        return _TaskStep(self, key)

    def step_failed(self) -> None:
        """Report the failure being handled, then mark the step failed.

        Reads the exception under handling instead of taking it as an argument,
        so a caller cannot mark a step failed and drop the reason - which left
        every migration task logging nothing but STEP-FAILED.
        """
        error = sys.exc_info()[1]
        if error is not None:
            print(f"Error: {error}", flush=True)
        if self._current_step:
            self.mark_step_failed(self._current_step)

    def mark_step_failed(self, key: str) -> None:
        if key in self._failed_steps:
            return
        self._failed_steps.add(key)
        print(f"STEP-FAILED {key},{time.time():.3f}", flush=True)

    def clear_step(self, key: str) -> None:
        if self._current_step == key:
            self._current_step = None

    def done(self) -> None:
        with self.step("done"):
            pass

    def report(self, message: str) -> None:
        print(message, flush=True)

    def require_production_privileges(self) -> None:
        from pilot.exceptions import BenchError
        from pilot.managers.nginx import NginxManager

        if self.bench.config.production.enabled and not NginxManager(self.bench).has_passwordless_sudo:
            raise BenchError("Production site operations require non-interactive system privileges.")

    @classmethod
    def main(cls) -> None:
        from pilot.internal.tasks.authoring import run_task_main

        run_task_main(cls)

    def run(self) -> None:
        raise NotImplementedError


class TaskRunner:
    def __init__(self, bench_root: Path) -> None:
        from pilot.internal.tasks.runner import runner_class

        self.__engine = runner_class()(bench_root)

    def run_task(
        self,
        task_type: type[Task],
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
        **args,
    ) -> str:
        return self.run(
            task_type.command,
            self._task_args(task_type, args),
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        )

    def submit_task(
        self,
        task_type: type[Task],
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
        **args,
    ) -> TaskSubmission:
        return self.submit(
            task_type.command,
            self._task_args(task_type, args),
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        )

    def run(
        self,
        command: str,
        args: dict,
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
    ) -> str:
        return self.__engine.run(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        )

    def submit(
        self,
        command: str,
        args: dict,
        callbacks: "TaskCallbacks | None" = None,
        idempotency_key: str | None = None,
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
    ) -> TaskSubmission:
        result = self.__engine.submit(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        )
        return TaskSubmission(result.task_id, result.created)

    def kill(self, task_id: str) -> None:
        self.__engine.kill(task_id)

    def _task_args(self, task_type: type[Task], args: dict) -> dict:
        valid = {
            field.name
            for field in fields(task_type)
            if field.init and field.name not in {"bench", "bench_root"}
        }
        valid.update(task_type.required_submit_args)
        unknown = set(args) - valid
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown args for {task_type.__name__}: {names}")
        return {key: value for key, value in args.items() if value is not None}


__all__ = ["Arg", "Task", "TaskRunner", "TaskSubmission", "step"]
