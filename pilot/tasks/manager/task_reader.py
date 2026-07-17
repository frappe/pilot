from __future__ import annotations

import re
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

from pilot.tasks.manager.models import TaskInfo, safe_task_failure
from pilot.tasks.manager.task_queue import TaskQueue
from pilot.tasks.manager.task_args import redact_task_args
from pilot.tasks.manager.task_state import (
    ACTIVE_TASK_STATUSES,
    TaskStatus,
    parse_task_status,
)
from pilot.tasks.manager.events import (
    TaskStreamEvent,
    done_event,
    output_event,
    status_event,
)
from pilot.tasks.timing import TASK_POLL_SECONDS
from pilot.exceptions import TaskNotFoundError
from pilot.secure_files import open_private

_TASK_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{6}$")

# Matches the RFC 5424 envelope the wrapper stamps onto output.log:
# <PRI>VERSION TIMESTAMP HOST APP-NAME PROCID MSGID STRUCTURED-DATA MESSAGE
_SYSLOG_RE = re.compile(r"^<\d+>\d+ \S+ \S+ \S+ \S+ \S+ \S+ (.*)$")


def _collapse_cr(line: str) -> str:
    """Simulate terminal carriage-return: \r resets to column 0, last write wins."""
    if '\r' not in line:
        return line
    parts = line.split('\r')
    return next((p for p in reversed(parts) if p.strip()), '')


def _strip_syslog_envelope(segment: str) -> str:
    match = _SYSLOG_RE.match(segment)
    return match.group(1) if match else segment


def _display_line(raw_line: str) -> str:
    """Turn a stored (syslog-enveloped) line into what the API/UI should see:
    the envelope stripped off every \r-redraw segment, then collapsed like a
    terminal would. output.log itself keeps the full syslog format so a log
    shipper can ingest it as-is — callers here never see the envelope."""
    stripped = '\r'.join(_strip_syslog_envelope(seg) for seg in raw_line.split('\r'))
    return _collapse_cr(stripped)


class TaskReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._queue = TaskQueue(bench_root)

    def list_tasks(self, limit: int | None = 50) -> list[TaskInfo]:
        tasks_dir = self._bench_root / "tasks"
        if not tasks_dir.exists():
            return []

        tasks: list[TaskInfo] = []
        queue_positions = self._queue.positions()
        for entry in tasks_dir.iterdir():
            if entry.is_dir() and _TASK_ID_PATTERN.match(entry.name):
                try:
                    tasks.append(_read_task_dir(self, entry, queue_positions))
                except Exception:
                    continue

        tasks.sort(key=lambda task: task.queued_at, reverse=True)
        return tasks if limit is None else tasks[:limit]

    def read_task(self, task_id: str) -> TaskInfo:
        if not _TASK_ID_PATTERN.match(task_id):
            raise TaskNotFoundError(f"Invalid task ID format: {task_id!r}")

        task_dir = self._bench_root / "tasks" / task_id
        if not task_dir.exists():
            raise TaskNotFoundError(f"Task not found: {task_id}")

        return _read_task_dir(self, task_dir, self._queue.positions())

    def read_output(self, task_id: str, lines: int | None = None) -> list[str]:
        self.read_task(task_id)  # validates task_id and existence
        output_path = self._bench_root / "tasks" / task_id / "output.log"
        if not output_path.exists():
            return []
        with open(output_path, "r", errors="replace", newline='') as f:
            text = f.read()
        all_lines = [_display_line(line) for line in text.split("\n")]
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
                    yield _display_line(line) + "\n"
            if pending:
                yield _display_line(pending)

    def stream_output(self, task_id: str) -> Generator[TaskStreamEvent, None, None]:
        task = self.read_task(task_id)
        output_path = task.output_path
        last_state = (task.status, task.queue_position)
        yield status_event(task.status.value, task.queue_position)

        open_private(output_path, "a").close()
        with open(output_path, "r", errors="replace", newline='') as log_file:
            # No seek: replay from the start so a fresh connection gets history too.
            # Raw current line, envelope and carriage returns and all. We only
            # strip the syslog envelope and resolve \r at emit time via
            # _display_line, so the live stream matches read_output and the
            # frontend exactly: a CRLF keeps its text, a progress line cleared
            # with \r + padding collapses to its last real segment instead of
            # leaking a row of spaces, and the UI never sees the raw envelope.
            cur = ''

            while True:
                chunk = log_file.read(8192)
                if chunk:
                    for ch in chunk:
                        if ch == '\n':
                            yield output_event(_display_line(cur))
                            cur = ''
                        else:
                            cur += ch
                    if cur:
                        yield output_event(_display_line(cur), overwrite=True)
                    continue

                task = self.read_task(task_id)
                current_state = (task.status, task.queue_position)
                if current_state != last_state:
                    yield status_event(task.status.value, task.queue_position)
                    last_state = current_state

                if task.status not in ACTIVE_TASK_STATUSES:
                    if cur:
                        yield output_event(_display_line(cur))
                    failure = task.as_dict()["failure"]
                    yield done_event(task.status.value, task.exit_code, failure)
                    return

                time.sleep(TASK_POLL_SECONDS)

def _read_task_dir(
    reader: TaskReader,
    task_dir: Path,
    queue_positions: dict[str, int],
) -> TaskInfo:
    import json

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
        datetime.fromisoformat(meta["started_at"])
        if meta.get("started_at") is not None
        else None
    )
    finished_at = (
        datetime.fromisoformat(meta["finished_at"])
        if meta.get("finished_at") is not None
        else None
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
            queue_positions.get(meta["task_id"])
            if effective_status == TaskStatus.QUEUED
            else None
        ),
        failure=safe_task_failure(meta.get("failure"), effective_status),
    )
