from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import IO, Iterator

from pilot.internal.atomic_file import (
    atomic_write_private_text,
    exclusive_file_lock,
    replace_private_text_locked,
)
from pilot.secure_files import make_private_directory, open_private


class WorkerLock:
    def __init__(self, handle: IO) -> None:
        self._handle = handle

    @classmethod
    def try_acquire(cls, path: Path) -> WorkerLock | None:
        handle = open_private(path, "a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return cls(handle)

    def release(self) -> None:
        if self._handle.closed:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def __enter__(self) -> WorkerLock:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class WorkerStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"


class WorkerIntent(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass(frozen=True)
class WorkerState:
    status: WorkerStatus
    pid: int | None
    current_task_id: str | None
    updated_at: datetime


class WorkerStore:
    def __init__(self, bench_root: Path) -> None:
        self.tasks_root = Path(bench_root) / "tasks"
        self.lock_path = self.tasks_root / "worker.lock"
        self.pid_path = self.tasks_root / "worker.pid"
        self.state_path = self.tasks_root / "worker-state.json"
        self.intent_path = self.tasks_root / "worker-control.json"

    def ensure_layout(self) -> None:
        make_private_directory(self.tasks_root, parents=True)
        with open_private(self.lock_path, "a"):
            pass

    def try_acquire(self) -> WorkerLock | None:
        self.ensure_layout()
        return WorkerLock.try_acquire(self.lock_path)

    def write_pid(self, pid: int | None) -> None:
        self.ensure_layout()
        atomic_write_private_text(self.pid_path, "" if pid is None else str(pid))

    def read_pid(self) -> int | None:
        if not self.pid_path.exists():
            return None
        value = self.pid_path.read_text(encoding="utf-8").strip()
        return int(value) if value else None

    def write_intent(self, intent: WorkerIntent) -> None:
        self.ensure_layout()
        with exclusive_file_lock(self.intent_path):
            replace_private_text_locked(
                self.intent_path,
                json.dumps({"desired": intent.value}, indent=2),
            )

    def read_intent(self) -> WorkerIntent:
        with self.locked_intent() as intent:
            return intent

    @contextmanager
    def locked_intent(self) -> Iterator[WorkerIntent]:
        self.ensure_layout()
        with exclusive_file_lock(self.intent_path):
            yield self._read_intent_locked()

    def write_state(
        self,
        status: WorkerStatus,
        pid: int | None,
        current_task_id: str | None = None,
    ) -> WorkerState:
        self.ensure_layout()
        state = WorkerState(
            status=status,
            pid=pid,
            current_task_id=current_task_id,
            updated_at=datetime.now(timezone.utc),
        )
        payload = {
            "status": state.status.value,
            "pid": state.pid,
            "current_task_id": state.current_task_id,
            "updated_at": state.updated_at.isoformat(),
        }
        atomic_write_private_text(self.state_path, json.dumps(payload, indent=2))
        return state

    def read_state(self) -> WorkerState | None:
        if not self.state_path.exists():
            return None
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return WorkerState(
            status=WorkerStatus(payload["status"]),
            pid=payload.get("pid"),
            current_task_id=payload.get("current_task_id"),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )

    def _read_intent_locked(self) -> WorkerIntent:
        if not self.intent_path.exists():
            return WorkerIntent.RUNNING
        payload = json.loads(self.intent_path.read_text(encoding="utf-8"))
        return WorkerIntent(payload["desired"])
