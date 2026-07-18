from __future__ import annotations

from pilot.internal.tasks.args import task_requires_secrets as _task_requires_secrets


def task_requires_secrets(command: str) -> bool:
    return _task_requires_secrets(command)
