from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from admin.backend.tasks.manager.task_state import (
    TaskStatus,
    parse_task_status,
    validate_task_transition,
)
from pilot.exceptions import TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.secure_files import make_private_directory, open_private


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
        task_id = str(metadata["task_id"])
        make_private_directory(self.tasks_root, parents=True)
        with exclusive_file_lock(self._lock_target):
            task_dir = self.task_dir(task_id)
            if task_dir.exists():
                raise FileExistsError(f"Task already exists: {task_id}")
            return self._publish_task(task_dir, metadata, private_files or {})

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

    def locked(self):
        make_private_directory(self.tasks_root, parents=True)
        return exclusive_file_lock(self._lock_target)

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

    def _write_metadata(self, task_id: str, metadata: Mapping[str, object]) -> None:
        replace_private_text_locked(
            self.task_dir(task_id) / "meta.json",
            json.dumps(metadata, indent=2),
        )

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
