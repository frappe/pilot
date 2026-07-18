from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar

from pilot.cli_args import Arg

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class _TaskStep:
    def __init__(self, task, key: str) -> None:
        self.task = task
        self.key = key

    def __enter__(self) -> "_TaskStep":
        return self

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
class BaseTask:
    """Base class for dataclass task modules discovered by pilot.tasks."""

    command: ClassVar[str] = ""
    has_done_step: ClassVar[bool] = True
    required_submit_args: ClassVar[tuple[str, ...]] = ()

    bench: "Bench"
    bench_root: Path

    def __post_init__(self) -> None:
        self._current_step: str | None = None
        self._failed_steps: set[str] = set()

    def step(self, key: str, label: str = "") -> _TaskStep:
        self._current_step = key
        print(f"STEP {key},{time.time():.3f} {label}", flush=True)
        return _TaskStep(self, key)

    def step_failed(self) -> None:
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
        from pilot.managers.platform import has_passwordless_sudo

        if self.bench.config.production.enabled and not has_passwordless_sudo():
            raise BenchError(
                "Production site operations require non-interactive system privileges."
            )

    def record_audit(self, category: str, fields: dict) -> None:
        from pilot.core.audit_log import AuditLog

        try:
            AuditLog(self.bench).append(category, fields)
        except Exception as exc:
            print(f"Audit log update skipped due to error: {exc!s}")

    @classmethod
    def main(cls) -> None:
        from pilot.internal.tasks.authoring import run_task_main

        run_task_main(cls)

    def run(self) -> None:
        raise NotImplementedError

__all__ = ["Arg", "BaseTask", "step"]
