from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path

from pilot.exceptions import TaskNotFoundError
from pilot.internal.atomic_file import replace_private_text_locked
from pilot.utils import open_private

_QUEUED_STATUS = "queued"
_TASK_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{6}$")


class TaskFiles:
    def __init__(self, tasks_root: Path) -> None:
        self.tasks_root = Path(tasks_root)

    def task_dir(self, task_id: str) -> Path:
        if not self.is_valid_task_id(task_id):
            raise TaskNotFoundError(f"Invalid task ID: {task_id!r}")
        return self.tasks_root / task_id

    def existing_task_dir(self, task_id: str) -> Path:
        task_dir = self.task_dir(task_id)
        if not task_dir.is_dir():
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task_dir

    def task_dirs(self) -> Iterator[Path]:
        if not self.tasks_root.exists():
            return
        for task_dir in self.tasks_root.iterdir():
            if not self.is_valid_task_id(task_dir.name) or task_dir.is_symlink() or not task_dir.is_dir():
                continue
            yield task_dir

    def write_metadata(self, task_id: str, metadata: Mapping[str, object]) -> None:
        replace_private_text_locked(
            self.task_dir(task_id) / "meta.json",
            json.dumps(metadata, indent=2),
        )

    def remove_files(self, task_id: str, names: tuple[str, ...]) -> None:
        task_dir = self.existing_task_dir(task_id)
        for name in names:
            self.validate_private_name(name)
            (task_dir / name).unlink(missing_ok=True)

    def publish_task(
        self,
        task_dir: Path,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
    ) -> Path:
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{task_dir.name}.", dir=self.tasks_root))
        temporary_dir.chmod(0o700)
        try:
            self.write_staged(temporary_dir / "meta.json", json.dumps(metadata, indent=2))
            self.write_staged(temporary_dir / "status", _QUEUED_STATUS)
            for name, content in private_files.items():
                self.validate_private_name(name)
                self.write_staged(temporary_dir / name, content)
            self.fsync_directory(temporary_dir)
            os.replace(temporary_dir, task_dir)
            self.fsync_directory(self.tasks_root)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise
        return task_dir

    @staticmethod
    def has_cleanup_pending(task_dir: Path) -> bool:
        return (task_dir / "callbacks.json").exists() or (task_dir / "process.json").exists()

    @staticmethod
    def read_queue_sequence(task_dir: Path) -> int | None:
        try:
            metadata = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
            value = metadata.get("queue_sequence")
            return value if isinstance(value, int) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def completion_key(metadata: dict, task_id: str) -> tuple[float, str]:
        for field in ("finished_at", "queued_at", "started_at"):
            value = metadata.get(field)
            if not isinstance(value, str):
                continue
            try:
                return (datetime.fromisoformat(value).timestamp(), task_id)
            except ValueError:
                continue
        return (float("inf"), task_id)

    @staticmethod
    def validate_private_name(name: str) -> None:
        if not name or name in {".", ".."} or Path(name).name != name:
            raise ValueError(f"Invalid task filename: {name!r}")

    @staticmethod
    def is_valid_task_id(task_id: str) -> bool:
        return bool(_TASK_ID_PATTERN.match(task_id))

    @staticmethod
    def write_staged(path: Path, content: str) -> None:
        with open_private(path) as staged_file:
            staged_file.write(content)
            staged_file.flush()
            os.fsync(staged_file.fileno())

    @staticmethod
    def fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
