from __future__ import annotations

import json
from typing import Literal, TypedDict


class OutputEvent(TypedDict):
    type: Literal["line", "overwrite"]
    line: str


class DoneEvent(TypedDict):
    type: Literal["done"]
    exit_code: int | None
    status: str
    failure: dict | None


class StatusEvent(TypedDict):
    type: Literal["status"]
    status: str
    queue_position: int | None


TaskStreamEvent = OutputEvent | StatusEvent | DoneEvent


def output_event(line: str, *, overwrite: bool = False) -> OutputEvent:
    return {"type": "overwrite" if overwrite else "line", "line": line}


def status_event(status: str, queue_position: int | None) -> StatusEvent:
    return {
        "type": "status",
        "status": status,
        "queue_position": queue_position,
    }


def done_event(status: str, exit_code: int | None, failure: dict | None) -> DoneEvent:
    return {
        "type": "done",
        "status": status,
        "exit_code": exit_code,
        "failure": failure,
    }


def sse_message(event: TaskStreamEvent, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    payload = json.dumps(event, separators=(",", ":"))
    return f"{prefix}data: {payload}\n\n"
