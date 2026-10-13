from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked, shared_file_lock
from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.state import parse_task_status, validate_task_transition
from pilot.utils import make_private_directory

HOST_RESOURCE_PREFIX = "host:"
_HOST_RESOURCE_LOCK = ".host-resources"
_BENCH_DROP_EXCLUSION_LOCK = "drop-bench-exclusion"
_DROP_COORDINATED_COMMANDS = frozenset(
    {
        "backup-site",
        "build",
        "clear-cache",
        "delete-backup",
        "drop-site",
        "get-and-install-app",
        "install-app",
        "migrate",
        "migration-backup",
        "new-site",
        "new-site-from-backup",
        "reinstall-site",
        "rename-site",
        "restore-site",
        "revert-site",
        "setup-letsencrypt",
        "uninstall-app",
    }
)
_ADDITIONAL_SITE_ARG = {
    "new-site": "name",
    "new-site-from-backup": "name",
    "restore-site": "source_site",
    "rename-site": "new_name",
}


def _host_keys(keys: list[str]) -> set[str]:
    return {key for key in keys if key.startswith(HOST_RESOURCE_PREFIX)}


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
        resource_key: str | list[str] | None = None,
        resource_handoff_from: str | None = None,
    ) -> Path:
        keys = self._normalize_keys(resource_key)
        make_private_directory(self.tasks_root, parents=True)
        with (
            self._host_resource_lock(keys),
            exclusive_file_lock(self._lock_target),
            self._bench_drop_exclusion_lock(metadata, shared=False),
            self._drop_exclusion_locks(self._drop_site_names(metadata), shared=True),
            self._site_resource_locks(keys),
        ):
            self._reject_active_resource_locked(keys, resource_handoff_from)
            self._reject_host_resources_elsewhere(keys)
            return self._create_queued_locked(
                self._with_resource_keys(metadata, keys),
                private_files or {},
            )

    def create_idempotent_queued(
        self,
        metadata: Mapping[str, object],
        private_files: Mapping[str, str],
        idempotency_digest: str,
        request_fingerprint: str,
        resource_key: str | list[str] | None = None,
    ) -> TaskCreation:
        keys = self._normalize_keys(resource_key)
        make_private_directory(self.tasks_root, parents=True)
        with (
            self._host_resource_lock(keys),
            exclusive_file_lock(self._lock_target),
            self._bench_drop_exclusion_lock(metadata, shared=False),
            self._drop_exclusion_locks(self._drop_site_names(metadata), shared=True),
            self._site_resource_locks(keys),
        ):
            existing = self._active_idempotent_task_locked(idempotency_digest)
            if existing is not None:
                existing_fingerprint = self.read_metadata(existing).get("request_fingerprint")
                if existing_fingerprint != request_fingerprint:
                    raise TaskConflictError("Idempotency key is already in use for another active task")
                return TaskCreation(existing, self.task_dir(existing), False)

            self._reject_active_resource_locked(keys)
            self._reject_host_resources_elsewhere(keys)
            stored_metadata = dict(metadata)
            stored_metadata["idempotency_digest"] = idempotency_digest
            stored_metadata["request_fingerprint"] = request_fingerprint
            stored_metadata = self._with_resource_keys(stored_metadata, keys)
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
                task_dir.name for task_dir in self._files.task_dirs() if (task_dir / "process.json").exists()
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

    @contextmanager
    def reserve_direct_site(self, site_name: str) -> Iterator[None]:
        key = f"site:{site_name.lower()}"
        make_private_directory(self.tasks_root, parents=True)
        with (
            self._bench_drop_exclusion_lock(None, shared=True),
            self._drop_exclusion_locks([site_name], shared=False),
            self._site_resource_locks([key]),
        ):
            with exclusive_file_lock(self._lock_target):
                self._reject_active_resource_locked([key])
                self._reject_active_drop_work_locked(site_name)
            yield

    @contextmanager
    def reserve_inline_site_write(self, site_name: str) -> Iterator[None]:
        with self.reserve_site_writes([site_name]):
            yield

    @contextmanager
    def reserve_site_writes(self, site_names: list[str]) -> Iterator[None]:
        make_private_directory(self.tasks_root, parents=True)
        with self._drop_exclusion_locks(site_names, shared=True):
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

    def _reject_active_resource_locked(
        self,
        resource_keys: list[str],
        resource_handoff_from: str | None = None,
    ) -> None:
        if not resource_keys:
            return
        requested = set(resource_keys)
        handoff_found = resource_handoff_from is None
        for task_id, held in self._active_resources_locked():
            if task_id == resource_handoff_from:
                if not requested.issubset(held):
                    raise TaskConflictError("Resource handoff does not match the active task's resources")
                handoff_found = True
                continue
            conflict = requested & held
            if conflict:
                raise TaskConflictError(
                    f"Another active task is already using resource {sorted(conflict)[0]!r}"
                )
        if not handoff_found:
            raise TaskConflictError("Resource handoff task is not active")

    def _reject_active_drop_work_locked(self, site_name: str) -> None:
        wanted = site_name.lower()
        for task_dir in self._files.task_dirs():
            try:
                status = self.read_status(task_dir.name)
                if not status.is_active and not self._files.has_cleanup_pending(task_dir):
                    continue
                metadata = self.read_metadata(task_dir.name)
            except (OSError, ValueError, TaskNotFoundError):
                continue
            if metadata.get("command") == "remove-app":
                raise TaskConflictError("An app removal task is active across this bench")
            if wanted in self._drop_site_names(metadata):
                raise TaskConflictError(f"Another active task is already using resource 'site:{wanted}'")

    def _host_resource_lock(self, keys: list[str]) -> AbstractContextManager:
        """Serialize hostname claims before taking the bench lock."""
        if not _host_keys(keys):
            return nullcontext()
        return exclusive_file_lock(self.bench_root.parent / _HOST_RESOURCE_LOCK)

    @contextmanager
    def _bench_drop_exclusion_lock(
        self, metadata: Mapping[str, object] | None, *, shared: bool
    ) -> Iterator[None]:
        if not shared and (metadata is None or metadata.get("command") != "remove-app"):
            yield
            return
        path = self.tasks_root / _BENCH_DROP_EXCLUSION_LOCK
        lock = shared_file_lock if shared else exclusive_file_lock
        try:
            with lock(path, blocking=False):
                yield
        except BlockingIOError as error:
            raise TaskConflictError("An app removal conflicts with site deletion in this bench") from error

    @contextmanager
    def _site_resource_locks(self, keys: list[str]) -> Iterator[None]:
        with ExitStack() as locks:
            for key in sorted(set(keys)):
                if not key.startswith("site:"):
                    continue
                digest = sha256(key.encode()).hexdigest()
                try:
                    locks.enter_context(
                        exclusive_file_lock(self.tasks_root / f"site-resource-{digest}", blocking=False)
                    )
                except BlockingIOError as error:
                    raise TaskConflictError(f"Another operation is already using resource {key!r}") from error
            yield

    @contextmanager
    def _drop_exclusion_locks(self, site_names: list[str], *, shared: bool) -> Iterator[None]:
        with ExitStack() as locks:
            for site_name in sorted({name.lower() for name in site_names}):
                key = f"site:{site_name}"
                digest = sha256(key.encode()).hexdigest()
                path = self.tasks_root / f"drop-exclusion-{digest}"
                try:
                    lock = (
                        shared_file_lock(path, blocking=False)
                        if shared
                        else exclusive_file_lock(path, blocking=False)
                    )
                    locks.enter_context(lock)
                except BlockingIOError as error:
                    raise TaskConflictError(f"Another operation is already using resource {key!r}") from error
            yield

    def _reject_host_resources_elsewhere(self, keys: list[str]) -> None:
        """Reject hostname claims held by an active task on another bench."""
        wanted = _host_keys(keys)
        if not wanted:
            return
        for store in self._sibling_stores():
            for _task_id, held in store._active_resources_locked():
                if conflict := wanted & held:
                    raise TaskConflictError(
                        f"Another bench's active task is already using resource {sorted(conflict)[0]!r}"
                    )

    def _sibling_stores(self) -> Iterator["TaskStore"]:
        parent = self.bench_root.parent
        if not parent.is_dir():
            return
        me = self.bench_root.resolve()
        for sibling in parent.iterdir():
            if sibling.is_dir() and sibling.resolve() != me and (sibling / "tasks").is_dir():
                yield TaskStore(sibling)

    def _active_resources_locked(self) -> Iterator[tuple[str, set[str]]]:
        for task_dir in self._files.task_dirs():
            task_id = task_dir.name
            try:
                status = self.read_status(task_id)
                if not status.is_active and not self._files.has_cleanup_pending(task_dir):
                    continue
                yield task_id, set(self._task_resource_keys(self.read_metadata(task_id)))
            except (OSError, ValueError, TaskNotFoundError):
                continue

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
    def _normalize_keys(resource_key: str | list[str] | None) -> list[str]:
        if resource_key is None:
            return []
        if isinstance(resource_key, str):
            return [resource_key]
        return list(resource_key)

    @staticmethod
    def _drop_site_names(metadata: Mapping[str, object]) -> list[str]:
        command = metadata.get("command")
        if command not in _DROP_COORDINATED_COMMANDS:
            return []
        args = metadata.get("args")
        if not isinstance(args, dict):
            return []
        names: list[str] = []
        for field in ("site", _ADDITIONAL_SITE_ARG.get(command)):
            if isinstance(name := args.get(field), str) and name:
                names.append(name.lower())
        if isinstance(sites := args.get("sites"), list):
            names.extend(name.lower() for name in sites if isinstance(name, str) and name)
        return sorted(set(names))

    @staticmethod
    def _task_resource_keys(metadata: Mapping[str, object]) -> list[str]:
        keys = metadata.get("resource_keys")
        if isinstance(keys, list):
            return keys
        single = metadata.get("resource_key")
        return [single] if isinstance(single, str) else []

    @staticmethod
    def _with_resource_keys(metadata: Mapping[str, object], keys: list[str]) -> dict[str, object]:
        stored_metadata = dict(metadata)
        if keys:
            stored_metadata["resource_keys"] = list(keys)
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
