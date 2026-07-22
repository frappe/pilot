from __future__ import annotations

from pilot.internal.tasks.args import task_has_secrets as _task_has_secrets


def task_has_secrets(command: str) -> bool:
    return _task_has_secrets(command)
