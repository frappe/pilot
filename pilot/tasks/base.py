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


def step(key: str, label: str | Callable[[Any], str] = "", icon: str = ""):
    """Wrap a task method in a UI step, with an optional icon class for the dashboard."""

    def decorator(func):
        """Wrap the method in a step."""

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            """Run the method inside its step."""
            resolved_label = label(self) if callable(label) else label
            with self.step(key, resolved_label, icon):
                return func(self, *args, **kwargs)

        return wrapper

    return decorator


@dataclass(kw_only=True)
class Task:
    """Dataclass task module discovered by pilot.tasks."""

    command: ClassVar[str] = ""
    timeout_seconds: ClassVar[int] = 3600
    has_done_step: ClassVar[bool] = True
    required_submit_args: ClassVar[tuple[str, ...]] = ()
    # Audit "who queued this" on queue. Turn off for frequent polling/read-only tasks.
    audit_on_queue: ClassVar[bool] = True
    # Turn off for tasks that leave partial state behind when killed mid-run.
    # Queued tasks can always be cancelled - nothing has run yet.
    is_cancellable_while_running: ClassVar[bool] = True
    # Turn off to keep a task out of listings; it stays readable by task id.
    is_listed: ClassVar[bool] = True
    # Fixed resources the task always holds, such as "database-server".
    static_resource_keys: ClassVar[tuple[str, ...]] = ()
    # Non-sensitive queue args safe to record in the audit entry.
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = (
        "site",
        "app",
        "name",
        "new_name",
        "repo",
        "branch",
        "marketplace_app",
        "no_backup",
    )

    bench: "Bench"
    bench_root: Path

    @property
    def running_task_id(self) -> str:
        """This task's id while a task worker runs it; empty when run directly."""
        import os

        from pilot.internal.tasks.process import TASK_ID_ENV

        return os.environ.get(TASK_ID_ENV, "")

    @property
    def resource_keys(self) -> list[str]:
        """The resources this task holds while active; a task that needs one of them is refused."""
        return list(self.static_resource_keys)

    @staticmethod
    def get_site_resource_key(name: str) -> str:
        """The resource key of a site."""
        return f"site:{name.lower()}"

    @staticmethod
    def get_host_resource_key(host: str) -> str:
        """The host-wide resource key of a hostname, shared by every bench on the host."""
        from pilot.internal.hostnames import normalize_host
        from pilot.internal.tasks.store import HOST_RESOURCE_PREFIX

        return f"{HOST_RESOURCE_PREFIX}{normalize_host(host)}"

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
        """Queue the task and return its ID. `resource_key` replaces the task's own `resource_keys`."""
        task = cls.get_queued_task(bench, args)
        task_id = bench.tasks.run_task(
            cls,
            callbacks=task.get_callbacks(callbacks),
            idempotency_key=idempotency_key,
            resource_key=task.resource_keys if resource_key is None else resource_key,
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

    @staticmethod
    def get_class(command: str) -> type["Task"]:
        """The task class that runs a command; raise ValueError for an unknown one."""
        from pilot.internal.tasks.runner import task_registry

        jobs, _ = task_registry()
        if command not in jobs:
            raise ValueError(f"Unknown command: {command!r}")
        return jobs[command]

    @classmethod
    def get_queued_task(cls, bench: "Bench", args: dict) -> "Task":
        """The task these queue arguments describe; raise ValueError for a missing argument."""
        from pilot.internal.tasks.authoring import required_task_args

        if missing := [name for name in required_task_args(cls) if name not in args]:
            raise ValueError(f"Command {cls.command!r} requires arg {missing[0]!r}")
        return cls(bench=bench, bench_root=bench.path, **cls._constructor_args(args))

    def get_callbacks(self, explicit: "TaskCallbacks | None") -> "TaskCallbacks | None":
        """The callbacks this task declares, with explicit ones taking precedence."""
        from pilot.tasks.callbacks import task_callbacks_for

        declared = task_callbacks_for(self)
        if not declared:
            return explicit
        return {**declared, **explicit} if explicit else declared

    @classmethod
    def _constructor_args(cls, args: dict) -> dict:
        """The arguments the task constructor takes."""
        valid = {
            field.name for field in fields(cls) if field.init and field.name not in {"bench", "bench_root"}
        }
        return {key: value for key, value in args.items() if key in valid}

    def __post_init__(self) -> None:
        self._current_step: str | None = None
        self._failed_steps: set[str] = set()

    def step(self, key: str, label: str = "", icon: str = "") -> AbstractContextManager[None]:
        """Mark a step as started and its end on exit."""
        self._current_step = key
        marker = f"{key},{time.time():.3f}" + (f",{icon}" if icon else "")
        print(f"STEP {marker} {label}", flush=True)
        return _TaskStep(self, key)

    def step_failed(self) -> None:
        """Report the active exception, then mark the current step failed."""
        error = sys.exc_info()[1]
        if error is not None:
            print(f"Error: {error}", flush=True)
        if self._current_step:
            self.mark_step_failed(self._current_step)

    def mark_step_failed(self, key: str) -> None:
        """Mark a step as failed once."""
        if key in self._failed_steps:
            return
        self._failed_steps.add(key)
        print(f"STEP-FAILED {key},{time.time():.3f}", flush=True)

    def clear_step(self, key: str) -> None:
        """Forget the current step if it is this one."""
        if self._current_step == key:
            self._current_step = None

    def done(self) -> None:
        """Mark the task as done."""
        with self.step("done"):
            pass

    def report(self, message: str) -> None:
        """Print a progress message."""
        print(message, flush=True)

    def require_production_privileges(self) -> None:
        """Raise when production needs root access that is not available."""
        from pilot.exceptions import BenchError
        from pilot.managers.nginx import NginxManager

        if self.bench.config.production.enabled and not NginxManager(self.bench).has_passwordless_sudo:
            raise BenchError("Production site operations require non-interactive system privileges.")

    @classmethod
    def main(cls) -> None:
        """Run the task from the command line."""
        from pilot.internal.tasks.authoring import run_task_main

        run_task_main(cls)

    def run(self) -> None:
        """Run the task."""
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
        """Queue a task and return its ID."""
        return self.run(
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
        """Queue a command and return its ID."""
        return self.__engine.run(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
            resource_handoff_from=resource_handoff_from,
        )

    def find_idempotent_task(self, command: str, args: dict, key: str | None) -> str | None:
        """The task queued earlier with this idempotency key, if any."""
        return self.__engine.find_idempotent_task(command, args, key)

    def kill(self, task_id: str) -> None:
        """Kill a task."""
        self.__engine.kill(task_id)

    def _task_args(self, task_type: type[Task], args: dict) -> dict:
        """The arguments the task type takes."""
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


__all__ = ["Arg", "Task", "TaskRunner", "step"]
