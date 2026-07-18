from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from pilot.internal.tasks.args import redact_task_args
from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.output import display_line
from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.state import parse_task_status, safe_task_failure
from pilot.managers.task.models import (
    TaskInfo,
    TaskStatus,
)
from pilot.secure_files import open_private

_TASK_POLL_SECONDS = 0.5


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


class TaskReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._files = TaskFiles(self._bench_root / "tasks")
        self._queue = TaskQueue(bench_root)

    def list_tasks(self, limit: int | None = 50) -> list[TaskInfo]:
        tasks: list[TaskInfo] = []
        queue_positions = self._queue.positions()
        for entry in self._files.task_dirs():
            try:
                tasks.append(_read_task_dir(self, entry, queue_positions))
            except Exception as exc:
                logging.debug("Skipping unreadable task directory %s: %s", entry, exc)
                continue

        tasks.sort(key=lambda task: task.queued_at, reverse=True)
        return tasks if limit is None else tasks[:limit]

    def read_task(self, task_id: str) -> TaskInfo:
        task_dir = self._files.existing_task_dir(task_id)
        return _read_task_dir(self, task_dir, self._queue.positions())

    def read_output(self, task_id: str, lines: int | None = None) -> list[str]:
        self.read_task(task_id)  # validates task_id and existence
        output_path = self._bench_root / "tasks" / task_id / "output.log"
        if not output_path.exists():
            return []
        with open(output_path, "r", errors="replace", newline="") as f:
            text = f.read()
        all_lines = [display_line(line) for line in text.split("\n")]
        while all_lines and not all_lines[-1]:
            all_lines.pop()
        if lines is None:
            return all_lines
        return all_lines[-lines:]

    def iter_output(self, task_id: str) -> Generator[str, None, None]:
        task = self.read_task(task_id)
        if not task.output_path.exists():
            return
        with open(task.output_path, "r", errors="replace", newline="") as output:
            pending = ""
            while chunk := output.read(8192):
                lines = (pending + chunk).split("\n")
                pending = lines.pop()
                for line in lines:
                    yield display_line(line) + "\n"
            if pending:
                yield display_line(pending)

    def stream_output(self, task_id: str) -> Generator[TaskStreamEvent, None, None]:
        task = self.read_task(task_id)
        output_path = task.output_path
        last_state = (task.status, task.queue_position)
        yield status_event(task.status.value, task.queue_position)

        open_private(output_path, "a").close()
        with open(output_path, "r", errors="replace", newline="") as log_file:
            # No seek: replay from the start so a fresh connection gets history too.
            # Raw current line, envelope and carriage returns and all. We only
            # strip the syslog envelope and resolve \r at emit time via
            # display_line, so the live stream matches read_output and the
            # frontend exactly: a CRLF keeps its text, a progress line cleared
            # with \r + padding collapses to its last real segment instead of
            # leaking a row of spaces, and the UI never sees the raw envelope.
            cur = ""

            while True:
                chunk = log_file.read(8192)
                if chunk:
                    for ch in chunk:
                        if ch == "\n":
                            yield output_event(display_line(cur))
                            cur = ""
                        else:
                            cur += ch
                    if cur:
                        yield output_event(display_line(cur), overwrite=True)
                    continue

                task = self.read_task(task_id)
                current_state = (task.status, task.queue_position)
                if current_state != last_state:
                    yield status_event(task.status.value, task.queue_position)
                    last_state = current_state

                if not task.status.is_active:
                    if cur:
                        yield output_event(display_line(cur))
                    failure = task.as_dict()["failure"]
                    yield done_event(task.status.value, task.exit_code, failure)
                    return

                time.sleep(_TASK_POLL_SECONDS)


def _read_task_dir(
    reader: TaskReader,
    task_dir: Path,
    queue_positions: dict[str, int],
) -> TaskInfo:
    meta = json.loads((task_dir / "meta.json").read_text())

    pid: int | None = None
    pid_file = task_dir / "pid"
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())

    raw_status = "running"
    status_file = task_dir / "status"
    if status_file.exists():
        raw_status = status_file.read_text().strip()

    effective_status = parse_task_status(raw_status)

    queued_at_value = meta.get("queued_at") or meta.get("started_at")
    queued_at = datetime.fromisoformat(queued_at_value)
    started_at = (
        datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") is not None else None
    )
    finished_at = (
        datetime.fromisoformat(meta["finished_at"]) if meta.get("finished_at") is not None else None
    )

    return TaskInfo(
        task_id=meta["task_id"],
        command=meta["command"],
        args=redact_task_args(meta.get("args", {})),
        status=effective_status,
        pid=pid,
        queued_at=queued_at,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=meta.get("exit_code"),
        output_path=task_dir / "output.log",
        queue_position=(
            queue_positions.get(meta["task_id"]) if effective_status == TaskStatus.QUEUED else None
        ),
        failure=safe_task_failure(meta.get("failure"), effective_status),
    )
