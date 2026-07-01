from __future__ import annotations

import os
import re
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

from admin.backend.tasks.manager.models import TaskInfo
from pilot.exceptions import TaskNotFoundError

_TASK_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{6}$")
_POLL_INTERVAL = 0.5

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

    def list_tasks(self, limit: int = 50) -> list[TaskInfo]:
        tasks_dir = self._bench_root / "tasks"
        if not tasks_dir.exists():
            return []

        tasks: list[TaskInfo] = []
        for entry in tasks_dir.iterdir():
            if entry.is_dir() and _TASK_ID_PATTERN.match(entry.name):
                try:
                    tasks.append(_read_task_dir(self, entry))
                except Exception:
                    continue

        tasks.sort(key=lambda task: task.started_at, reverse=True)
        return tasks[:limit]

    def read_task(self, task_id: str) -> TaskInfo:
        if not _TASK_ID_PATTERN.match(task_id):
            raise TaskNotFoundError(f"Invalid task ID format: {task_id!r}")

        task_dir = self._bench_root / "tasks" / task_id
        if not task_dir.exists():
            raise TaskNotFoundError(f"Task not found: {task_id}")

        return _read_task_dir(self, task_dir)

    def read_output(self, task_id: str, lines: int | None = None) -> list[str]:
        self.read_task(task_id)  # validates task_id and existence
        output_path = self._bench_root / "tasks" / task_id / "output.log"
        if not output_path.exists():
            return []
        with open(output_path, "r", errors="replace", newline='') as f:
            text = f.read()
        all_lines = [_display_line(l) for l in text.split("\n")]
        while all_lines and not all_lines[-1]:
            all_lines.pop()
        if lines is None:
            return all_lines
        return all_lines[-lines:]

    def stream_output(self, task_id: str) -> Generator[str, None, None]:
        task = self.read_task(task_id)
        output_path = task.output_path

        output_path.touch()
        with open(output_path, "r", errors="replace", newline='') as log_file:
            log_file.seek(0, 2)  # seek to end
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
                            yield _display_line(cur)  # commit
                            cur = ''
                        else:
                            cur += ch
                    if cur:
                        yield f"__CR__:{_display_line(cur)}"  # partial: overwrite
                    continue

                status_path = self._bench_root / "tasks" / task_id / "status"
                raw_status = status_path.read_text().strip() if status_path.exists() else "running"
                pid = task.pid
                effective = self._effective_status(task_id, raw_status, pid)

                if effective != "running":
                    if cur:
                        yield _display_line(cur)  # commit trailing partial line

                    meta_path = self._bench_root / "tasks" / task_id / "meta.json"
                    exit_code: int | None = None
                    if meta_path.exists():
                        import json
                        meta = json.loads(meta_path.read_text())
                        exit_code = meta.get("exit_code")
                    yield f"__DONE__:{exit_code}"
                    return

                time.sleep(_POLL_INTERVAL)

    def _effective_status(self, task_id: str, raw_status: str, pid: int | None) -> str:
        if raw_status != "running":
            return raw_status
        if pid is None:
            return "killed"
        try:
            os.kill(pid, 0)
        except OSError:
            return "killed"
        return "running"


def _read_task_dir(reader: TaskReader, task_dir: Path) -> TaskInfo:
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

    effective_status = reader._effective_status(meta["task_id"], raw_status, pid)

    started_at = datetime.fromisoformat(meta["started_at"])
    finished_at = (
        datetime.fromisoformat(meta["finished_at"])
        if meta.get("finished_at") is not None
        else None
    )

    return TaskInfo(
        task_id=meta["task_id"],
        command=meta["command"],
        args=meta.get("args", {}),
        status=effective_status,
        pid=pid,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=meta.get("exit_code"),
        output_path=task_dir / "output.log",
    )
