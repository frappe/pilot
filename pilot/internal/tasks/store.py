from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.state import parse_task_status, validate_task_transition
from pilot.managers.task.models import TaskStatus
from pilot.secure_files import make_private_directory


@dataclass(frozen=True)
class TaskCreation:
    task_id: str
    task_dir: Path
    created: bool


class TaskStore:
    def __init__(self, bench_root: Path) -> None:
        self.bench_root = Path(bench_root)
        self.tasks_root = self.bench_root / "tasks"
        self._files = TaskFiles(self.tasks_root)
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
        task_dir = self._files.existing_task_dir(task_id)
        return json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))

    def read_status(self, task_id: str) -> TaskStatus:
        task_dir = self._files.existing_task_dir(task_id)
        return parse_task_status((task_dir / "status").read_text(encoding="utf-8").strip())

    def read_pid(self, task_id: str) -> int | None:
        pid_path = self._files.existing_task_dir(task_id) / "pid"
        if not pid_path.exists():
            return None
        return int(pid_path.read_text(encoding="utf-8").strip())

    def write_pid(self, task_id: str, pid: int) -> None:
        with self.locked():
            replace_private_text_locked(self._files.existing_task_dir(task_id) / "pid", str(pid))

    def write_process(
        self,
        task_id: str,
        pid: int,
        process_record: Mapping[str, object],
    ) -> None:
        with self.locked():
            task_dir = self._files.existing_task_dir(task_id)
            replace_private_text_locked(
                task_dir / "process.json",
                json.dumps(process_record, indent=2),
            )
            replace_private_text_locked(task_dir / "pid", str(pid))

    def read_process(self, task_id: str) -> dict | None:
        path = self._files.existing_task_dir(task_id) / "process.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def task_ids_with_process(self) -> list[str]:
        with self.locked():
            return sorted(
                task_dir.name
                for task_dir in self._files.task_dirs()
                if (task_dir / "process.json").exists()
            )

    def terminal_task_ids_with_callbacks(self) -> list[str]:
        with self.locked():
            task_ids = []
            for task_dir in self._files.task_dirs():
                if not (task_dir / "callbacks.json").exists():
                    continue
                if (task_dir / "process.json").exists():
                    continue
                try:
                    if self.read_status(task_dir.name).is_terminal:
                        task_ids.append(task_dir.name)
                except (OSError, ValueError, TaskNotFoundError):
                    continue
            return sorted(task_ids)

    def update_metadata(self, task_id: str, updates: Mapping[str, object]) -> dict:
        with self.locked():
            metadata = self.read_metadata(task_id)
            metadata.update(updates)
            self._files.write_metadata(task_id, metadata)
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
            self._files.write_metadata(task_id, metadata)
        replace_private_text_locked(
            self.task_dir(task_id) / "status",
            target.value,
        )
        return True

    def remove_private_files(self, task_id: str, *names: str) -> None:
        with self.locked():
            self._files.remove_files(task_id, names)

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
        return self._files.task_dir(task_id)

    @contextmanager
    def locked(self) -> Iterator[None]:
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            yield

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
        return self._files.publish_task(task_dir, stored_metadata, private_files)

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
        for task_dir in self._files.task_dirs():
            task_id = task_dir.name
            try:
                status = self.read_status(task_id)
                if not status.is_active and not (
                    include_cleanup_pending and self._files.has_cleanup_pending(task_dir)
                ):
                    continue
                metadata = self.read_metadata(task_id)
            except (OSError, ValueError, TaskNotFoundError):
                continue
            if metadata.get(key) == value:
                return task_id
        return None

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
        for task_dir in self._files.task_dirs():
            task_id = task_dir.name
            try:
                if not self.read_status(task_id).is_terminal:
                    continue
                if self._files.has_cleanup_pending(task_dir):
                    continue
                metadata = self.read_metadata(task_id)
                terminal.append((self._files.completion_key(metadata, task_id), task_id))
            except (OSError, ValueError, TaskNotFoundError):
                continue
        terminal.sort()
        return terminal

    def _next_queue_sequence_locked(self) -> int:
        sequence_path = self.tasks_root / "queue-sequence"
        if sequence_path.exists():
            current = int(sequence_path.read_text(encoding="utf-8").strip())
        else:
            current = max(
                (
                    value
                    for task_dir in self._files.task_dirs()
                    if (value := self._files.read_queue_sequence(task_dir)) is not None
                ),
                default=0,
            )
        sequence = current + 1
        replace_private_text_locked(sequence_path, str(sequence))
        return sequence
