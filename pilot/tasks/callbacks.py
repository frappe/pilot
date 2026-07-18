from __future__ import annotations

from typing import TypedDict


class TaskCallback(TypedDict):
    operation: str
    args: dict


class TaskCallbacks(TypedDict, total=False):
    on_success: TaskCallback | None
    on_failure: TaskCallback | None
    on_cancel: TaskCallback | None
