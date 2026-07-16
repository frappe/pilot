from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from pilot.tasks.manager.task_state import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
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
        resource_key: str | None = None,
    ) -> Path:
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            self._reject_active_resource_locked(resource_key)
            return self._create_queued_locked(
                self._with_resource_key(metadata, resource_key),
                private_files or {},
            )

    def create_idempotent_queued(
        self,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
        idempotency_digest: str,
        request_fingerprint: str,
        resource_key: str | None = None,
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

            self._reject_active_resource_locked(resource_key)
            stored_metadata = dict(metadata)
            stored_metadata["idempotency_digest"] = idempotency_digest
            stored_metadata["request_fingerprint"] = request_fingerprint
            stored_metadata = self._with_resource_key(stored_metadata, resource_key)
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

    def write_process(
        self,
        task_id: str,
        pid: int,
        process_record: Mapping[str, object],
    ) -> None:
        with self.locked():
            task_dir = self._existing_task_dir(task_id)
            replace_private_text_locked(
                task_dir / "process.json",
                json.dumps(process_record, indent=2),
            )
            replace_private_text_locked(task_dir / "pid", str(pid))

    def read_process(self, task_id: str) -> dict | None:
        path = self._existing_task_dir(task_id) / "process.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def task_ids_with_process(self) -> list[str]:
        with self.locked():
            return sorted(
                task_dir.name
                for task_dir in self.tasks_root.iterdir()
                if not task_dir.is_symlink() and task_dir.is_dir()
                if (task_dir / "process.json").exists()
            )

    def terminal_task_ids_with_callbacks(self) -> list[str]:
        with self.locked():
            task_ids = []
            for task_dir in self.tasks_root.iterdir():
                if task_dir.is_symlink() or not task_dir.is_dir():
                    continue
                if not (task_dir / "callbacks.json").exists():
                    continue
                if (task_dir / "process.json").exists():
                    continue
                try:
                    if self.read_status(task_dir.name) in TERMINAL_TASK_STATUSES:
                        task_ids.append(task_dir.name)
                except (OSError, ValueError, TaskNotFoundError):
                    continue
            return sorted(task_ids)

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

    def purge_terminal(self, limit: int) -> list[str]:
        if limit < 0:
            raise ValueError("Task retention limit must not be negative")
        with self.locked():
            terminal = self._terminal_tasks_locked()
            to_delete = terminal[: max(0, len(terminal) - limit)]
            for _, task_id in to_delete:
                shutil.rmtree(self.task_dir(task_id))
            return [task_id for _, task_id in to_delete]

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
        return self._active_task_with_metadata_locked(
            "idempotency_digest",
            idempotency_digest,
            include_cleanup_pending=False,
        )

    def _reject_active_resource_locked(self, resource_key: str | None) -> None:
        if resource_key is None:
            return
        existing = self._active_task_with_metadata_locked(
            "resource_key",
            resource_key,
            include_cleanup_pending=True,
        )
        if existing is not None:
            raise TaskConflictError(
                f"Another active task is already using resource {resource_key!r}"
            )

    def _active_task_with_metadata_locked(
        self,
        key: str,
        value: str,
        *,
        include_cleanup_pending: bool,
    ) -> str | None:
        for task_dir in self.tasks_root.iterdir():
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            task_id = task_dir.name
            try:
                status = self.read_status(task_id)
                if status not in ACTIVE_TASK_STATUSES and not (
                    include_cleanup_pending and self._cleanup_pending(task_dir)
                ):
                    continue
                metadata = self.read_metadata(task_id)
            except (OSError, ValueError, TaskNotFoundError):
                continue
            if metadata.get(key) == value:
                return task_id
        return None

    @staticmethod
    def _cleanup_pending(task_dir: Path) -> bool:
        return (task_dir / "callbacks.json").exists() or (
            task_dir / "process.json"
        ).exists()

    @staticmethod
    def _with_resource_key(
        metadata: Mapping[str, object], resource_key: str | None
    ) -> dict[str, object]:
        stored_metadata = dict(metadata)
        if resource_key is not None:
            stored_metadata["resource_key"] = resource_key
        return stored_metadata

    def _terminal_tasks_locked(self) -> list[tuple[tuple[float, str], str]]:
        terminal = []
        for task_dir in self.tasks_root.iterdir():
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            task_id = task_dir.name
            try:
                if self.read_status(task_id) not in TERMINAL_TASK_STATUSES:
                    continue
                if self._cleanup_pending(task_dir):
                    continue
                metadata = self.read_metadata(task_id)
                terminal.append((self._completion_key(metadata, task_id), task_id))
            except (OSError, ValueError, TaskNotFoundError):
                continue
        terminal.sort()
        return terminal

    @staticmethod
    def _completion_key(metadata: dict, task_id: str) -> tuple[float, str]:
        for field in ("finished_at", "queued_at", "started_at"):
            value = metadata.get(field)
            if not isinstance(value, str):
                continue
            try:
                return (datetime.fromisoformat(value).timestamp(), task_id)
            except ValueError:
                continue
        return (float("inf"), task_id)

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
