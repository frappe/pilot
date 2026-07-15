from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from admin.backend.tasks.manager.task_state import (
    ACTIVE_TASK_STATUSES,
    TaskStatus,
    parse_task_status,
    validate_task_transition,
)
from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.secure_files import make_private_directory, open_private


@dataclass(frozen=True)
class TaskCreation:
    task_id: str
    task_dir: Path
    created: bool


class TaskStore:
    def __init__(self, bench_root: Path) -> None:
        self.bench_root = Path(bench_root)
        self.tasks_root = self.bench_root / "tasks"
        self._lock_target = self.tasks_root / "store"

    def create_queued(
        self,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str] | None = None,
    ) -> Path:
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            return self._create_queued_locked(metadata, private_files or {})

    def create_idempotent_queued(
        self,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
        idempotency_digest: str,
        request_fingerprint: str,
    ) -> TaskCreation:
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            existing = self._active_idempotent_task_locked(idempotency_digest)
            if existing is not None:
                existing_fingerprint = self.read_metadata(existing).get("request_fingerprint")
                if existing_fingerprint != request_fingerprint:
                    raise TaskConflictError(
                        "Idempotency key is already in use for another active task"
                    )
                return TaskCreation(existing, self.task_dir(existing), False)

            stored_metadata = dict(metadata)
            stored_metadata["idempotency_digest"] = idempotency_digest
            stored_metadata["request_fingerprint"] = request_fingerprint
            task_dir = self._create_queued_locked(stored_metadata, private_files)
            return TaskCreation(str(metadata["task_id"]), task_dir, True)

    def read_metadata(self, task_id: str) -> dict:
        task_dir = self._existing_task_dir(task_id)
        return json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))

    def read_status(self, task_id: str) -> TaskStatus:
        task_dir = self._existing_task_dir(task_id)
        return parse_task_status((task_dir / "status").read_text(encoding="utf-8").strip())

    def read_pid(self, task_id: str) -> int | None:
        pid_path = self._existing_task_dir(task_id) / "pid"
        if not pid_path.exists():
            return None
        return int(pid_path.read_text(encoding="utf-8").strip())

    def write_pid(self, task_id: str, pid: int) -> None:
        with self.locked():
            replace_private_text_locked(self._existing_task_dir(task_id) / "pid", str(pid))

    def update_metadata(self, task_id: str, updates: Mapping[str, object]) -> dict:
        with self.locked():
            metadata = self.read_metadata(task_id)
            metadata.update(updates)
            self._write_metadata(task_id, metadata)
            return metadata

    def transition(
        self,
        task_id: str,
        expected: TaskStatus,
        target: TaskStatus,
        metadata_updates: Mapping[str, object] | None = None,
    ) -> bool:
        validate_task_transition(expected, target)
        with self.locked():
            return self.transition_locked(task_id, expected, target, metadata_updates)

    def transition_locked(
        self,
        task_id: str,
        expected: TaskStatus,
        target: TaskStatus,
        metadata_updates: Mapping[str, object] | None = None,
    ) -> bool:
        validate_task_transition(expected, target)
        current = self.read_status(task_id)
        if current != expected:
            return False
        if metadata_updates:
            metadata = self.read_metadata(task_id)
            metadata.update(metadata_updates)
            self._write_metadata(task_id, metadata)
        replace_private_text_locked(
            self.task_dir(task_id) / "status",
            target.value,
        )
        return True

    def remove_private_files(self, task_id: str, *names: str) -> None:
        with self.locked():
            task_dir = self._existing_task_dir(task_id)
            for name in names:
                self._validate_private_name(name)
                (task_dir / name).unlink(missing_ok=True)

    def task_dir(self, task_id: str) -> Path:
        if not task_id or task_id in {".", ".."} or Path(task_id).name != task_id:
            raise TaskNotFoundError(f"Invalid task ID: {task_id!r}")
        return self.tasks_root / task_id

    @contextmanager
    def locked(self) -> Iterator[None]:
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            yield

    def _publish_task(
        self,
        task_dir: Path,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
    ) -> Path:
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{task_dir.name}.", dir=self.tasks_root))
        temporary_dir.chmod(0o700)
        try:
            self._write_staged(temporary_dir / "meta.json", json.dumps(metadata, indent=2))
            self._write_staged(temporary_dir / "status", TaskStatus.QUEUED.value)
            for name, content in private_files.items():
                self._validate_private_name(name)
                self._write_staged(temporary_dir / name, content)
            self._fsync_directory(temporary_dir)
            os.replace(temporary_dir, task_dir)
            self._fsync_directory(self.tasks_root)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise
        return task_dir

    def _create_queued_locked(
        self,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
    ) -> Path:
        task_id = str(metadata["task_id"])
        task_dir = self.task_dir(task_id)
        if task_dir.exists():
            raise FileExistsError(f"Task already exists: {task_id}")
        stored_metadata = dict(metadata)
        stored_metadata["queue_sequence"] = self._next_queue_sequence_locked()
        return self._publish_task(task_dir, stored_metadata, private_files)

    def _active_idempotent_task_locked(self, idempotency_digest: str) -> str | None:
        for task_dir in self.tasks_root.iterdir():
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            try:
                if self.read_status(task_id) not in ACTIVE_TASK_STATUSES:
                    continue
                metadata = self.read_metadata(task_id)
            except (OSError, ValueError, TaskNotFoundError):
                continue
            if metadata.get("idempotency_digest") == idempotency_digest:
                return task_id
        return None

    def _write_metadata(self, task_id: str, metadata: Mapping[str, object]) -> None:
        replace_private_text_locked(
            self.task_dir(task_id) / "meta.json",
            json.dumps(metadata, indent=2),
        )

    def _next_queue_sequence_locked(self) -> int:
        sequence_path = self.tasks_root / "queue-sequence"
        if sequence_path.exists():
            current = int(sequence_path.read_text(encoding="utf-8").strip())
        else:
            current = max(
                (
                    value
                    for task_dir in self.tasks_root.iterdir()
                    if task_dir.is_dir()
                    if (value := self._read_queue_sequence(task_dir)) is not None
                ),
                default=0,
            )
        sequence = current + 1
        replace_private_text_locked(sequence_path, str(sequence))
        return sequence

    @staticmethod
    def _read_queue_sequence(task_dir: Path) -> int | None:
        try:
            metadata = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
            value = metadata.get("queue_sequence")
            return value if isinstance(value, int) else None
        except (OSError, ValueError):
            return None

    def _existing_task_dir(self, task_id: str) -> Path:
        task_dir = self.task_dir(task_id)
        if not task_dir.is_dir():
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task_dir

    @staticmethod
    def _validate_private_name(name: str) -> None:
        if not name or name in {".", ".."} or Path(name).name != name:
            raise ValueError(f"Invalid task filename: {name!r}")

    @staticmethod
    def _write_staged(path: Path, content: str) -> None:
        with open_private(path) as staged_file:
            staged_file.write(content)
            staged_file.flush()
            os.fsync(staged_file.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
