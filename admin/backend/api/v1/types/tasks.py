from __future__ import annotations

from typing import Literal, TypedDict

from pilot.managers.task import TaskStatus


class TaskFailurePayload(TypedDict):
    code: str
    message: str


class TaskPayload(TypedDict):
    task_id: str
    command: str
    args: dict
    status: TaskStatus
    pid: int | None
    queued_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    duration_seconds: float | None
    queue_position: int | None
    is_cancellable: bool
    failure: TaskFailurePayload | None


class DebugDeltaEvent(TypedDict):
    type: Literal["delta"]
    text: str


class DebugDoneEvent(TypedDict):
    type: Literal["done"]


class DebugErrorEvent(TypedDict):
    type: Literal["error"]
    message: str


class TaskWorker(TypedDict):
    active: bool
    uncertain: bool
    status: str
    desired: str
    queued_tasks: int
    running_tasks: int
