from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from pilot.internal.private_files import PrivateFile
from pilot.internal.tasks.args import redact_task_args
from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.state import parse_task_status, safe_task_failure
from pilot.managers.task.models import (
    TaskInfo,
    TaskStatus,
)

# A write is the only way to see that a client went away, so a quiet stream still writes.
HEARTBEAT_SECONDS = 15
# Tasks that write a site's site_config.json before its database is ready, mapped to the
# argument that names that site. A clone's `site` is its source, which stays up.
SITE_PROVISIONING_ARGS = {
    "new-site": "name",
    "new-site-from-backup": "name",
    "reinstall-site": "site",
    "clone-site": "name",
}


class TaskStep(TypedDict):
    key: str
    label: str
    status: Literal["running", "done", "failed"]
    started_at: float


class TaskProgress(TypedDict):
    steps: list[TaskStep]
    activity: str | None
    percent: int | None
    error: str | None


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
    is_cancellable: bool


TaskStreamEvent = OutputEvent | StatusEvent | DoneEvent


def output_event(line: str, *, overwrite: bool = False) -> OutputEvent:
    """An output line event."""
    return {"type": "overwrite" if overwrite else "line", "line": line}


def status_event(task: "TaskInfo") -> StatusEvent:
    """A task status event."""
    return {
        "type": "status",
        "status": task.status.value,
        "queue_position": task.queue_position,
        "is_cancellable": task.is_cancellable,
    }


def done_event(status: str, exit_code: int | None, failure: dict | None) -> DoneEvent:
    """A task done event."""
    return {
        "type": "done",
        "status": status,
        "exit_code": exit_code,
        "failure": failure,
    }


def sse_message(event: TaskStreamEvent, event_id: int | None = None) -> str:
    """The event as a server-sent event message."""
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    payload = json.dumps(event, separators=(",", ":"))
    return f"{prefix}data: {payload}\n\n"


def collapse_cr(line: str) -> str:
    """The last visible part of a line with carriage returns."""
    if "\r" not in line:
        return line
    parts = line.split("\r")
    return next((part for part in reversed(parts) if part.strip()), "")


def display_line(raw_line: str) -> str:
    """The line as shown to the user."""
    stripped = "\r".join(strip_syslog_envelope(segment) for segment in raw_line.split("\r"))
    return collapse_cr(stripped)


def strip_syslog_envelope(segment: str) -> str:
    """The segment without its syslog prefix."""
    match = re.match(r"^<\d+>\d+ \S+ \S+ \S+ \S+ \S+ \S+ (.*)$", segment)
    return match.group(1) if match else segment


def _split_percent(line: str) -> tuple[str, int | None]:
    """Split a progress bar line into its text and its percentage."""
    match = re.search(r"\s*:?\s*(?:\[[^\]]*\]\s*)?(\d{1,3})%\s*$", line)
    if not match:
        return line, None
    return line[: match.start()], min(int(match[1]), 100)


class TaskReader:
    step_re = re.compile(r"^STEP ([\w-]+),([\d.]+) ?(.*)$")
    step_failed_re = re.compile(r"^STEP-FAILED ([\w-]+),")

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._files = TaskFiles(self._bench_root / "tasks")
        self._queue = TaskQueue(bench_root)

    def list_tasks(self, limit: int | None = 50, include_unlisted: bool = False) -> list[TaskInfo]:
        """The newest tasks."""
        from pilot.internal.tasks.runner import is_command_listed

        tasks: list[TaskInfo] = []
        queue_positions = self._queue.positions()
        for entry in self._files.task_dirs():
            try:
                task = _read_task_dir(self, entry, queue_positions)
            except Exception as exc:
                logging.debug("Skipping unreadable task directory %s: %s", entry, exc)
                continue
            if include_unlisted or is_command_listed(task.command):
                tasks.append(task)

        tasks.sort(key=lambda task: task.queued_at, reverse=True)
        return tasks if limit is None else tasks[:limit]

    def get_provisioning_site_names(self) -> set[str]:
        """Sites an active task is still creating, so neither ready nor broken yet."""
        return {
            name
            for task in self.list_tasks(limit=None, include_unlisted=True)
            if task.status.is_active
            and (key := SITE_PROVISIONING_ARGS.get(task.command))
            and (name := task.args.get(key))
        }

    def read_task(self, task_id: str) -> TaskInfo:
        """One task; raise when it does not exist."""
        task_dir = self._files.existing_task_dir(task_id)
        return _read_task_dir(self, task_dir, self._queue.positions())

    def read_output(self, task_id: str, lines: int | None = None) -> list[str]:
        """The task output lines, or the last ones."""
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

    def get_progress(self, task: TaskInfo) -> TaskProgress:
        """The steps from the task's STEP markers, the running step's latest output line,
        and the task's last output line when it failed."""
        steps: list[TaskStep] = []
        last_line = step_line = ""
        for line in self.read_output(task.task_id):
            if match := self.step_re.match(line):
                if steps and steps[-1]["status"] == "running":
                    steps[-1]["status"] = "done"
                step_line = ""
                if match[1] != "done":
                    key, started_at, label = match.groups()
                    steps.append(
                        {"key": key, "label": label, "status": "running", "started_at": float(started_at)}
                    )
            elif match := self.step_failed_re.match(line):
                steps = [{**step, "status": "failed"} if step["key"] == match[1] else step for step in steps]
            elif line.strip():
                last_line = step_line = line.strip()

        # A task can end without a closing marker, so its last step takes the task's outcome.
        if steps and steps[-1]["status"] == "running" and not task.status.is_active:
            steps[-1]["status"] = "done" if task.status == TaskStatus.SUCCESS else "failed"

        is_running = bool(steps) and steps[-1]["status"] == "running"
        activity, percent = _split_percent(step_line) if is_running else ("", None)
        return {
            "steps": steps,
            "activity": activity[:200] or None,
            "percent": percent,
            "error": last_line if task.status == TaskStatus.FAILED else None,
        }

    def iter_output(self, task_id: str) -> Generator[str, None, None]:
        """Yield the task output lines."""
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

    def stream_output(
        self, task_id: str, should_stop: Callable[[], bool] = lambda: False
    ) -> Generator[TaskStreamEvent | None, None, None]:
        """Events until the task ends or `should_stop`; the client resumes by Last-Event-ID.
        None is a heartbeat, sent when the task writes nothing for HEARTBEAT_SECONDS."""
        task = self.read_task(task_id)
        output_path = task.output_path
        last_state = (task.status, task.queue_position)
        yield status_event(task)

        PrivateFile(output_path).open("a").close()
        with open(output_path, "r", errors="replace", newline="") as log_file:
            cur = ""
            last_write = time.monotonic()
            while True:
                chunk = log_file.read(8192)
                if chunk:
                    cur = yield from self._stream_chunk(chunk, cur)
                    last_write = time.monotonic()
                    continue

                task = self.read_task(task_id)
                current_state = (task.status, task.queue_position)
                if current_state != last_state:
                    yield status_event(task)
                    last_state = current_state

                if not task.status.is_active:
                    yield from self._stream_done(task, cur)
                    return
                if should_stop():
                    return
                if time.monotonic() - last_write >= HEARTBEAT_SECONDS:
                    yield None
                    last_write = time.monotonic()

                time.sleep(0.5)

    def _stream_chunk(self, chunk: str, cur: str) -> Generator[TaskStreamEvent, None, str]:
        """Yield an event for each finished line; return the unfinished line."""
        for ch in chunk:
            if ch == "\n":
                yield output_event(display_line(cur))
                cur = ""
            else:
                cur += ch
        if cur:
            yield output_event(display_line(cur), overwrite=True)
        return cur

    def _stream_done(
        self,
        task: TaskInfo,
        cur: str,
    ) -> Generator[TaskStreamEvent, None, None]:
        """Yield the last line and the done event."""
        if cur:
            yield output_event(display_line(cur))
        failure = task.as_dict()["failure"]
        yield done_event(task.status.value, task.exit_code, failure)


def _read_task_dir(
    reader: TaskReader,
    task_dir: Path,
    queue_positions: dict[str, int],
) -> TaskInfo:
    """The task from its directory."""
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
    started_at = datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") is not None else None
    finished_at = datetime.fromisoformat(meta["finished_at"]) if meta.get("finished_at") is not None else None

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
