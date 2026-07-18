from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from pilot.exceptions import BenchError, TaskNotFoundError, TaskNotRunningError
from pilot.internal.tasks.callbacks import run_stored_callback, trigger_for_task_status
from pilot.internal.tasks.process_identity import (
    ProcessIdentity,
    ProcessInspector,
    ProcessOwnership,
    TaskProcessRecord,
)
from pilot.internal.tasks.store import TaskStore
from pilot.managers.platform import NONINTERACTIVE_PRIVILEGES_ENV
from pilot.managers.task.models import TaskStatus

_READY_FD_ENV = "BENCH_TASK_READY_FD"
_LAUNCH_ID_ENV = "BENCH_TASK_LAUNCH_ID"
CANCEL_GRACE_SECONDS = 3.0
_PROCESS_EXIT_POLL_SECONDS = 0.05


class TaskProcessStartError(BenchError):
    pass


class TaskProcess:
    def __init__(self, bench_root: Path) -> None:
        self._store = TaskStore(bench_root)
        self._inspector = ProcessInspector()

    def start(self, task_id: str) -> subprocess.Popen:
        task_dir = self._store.task_dir(task_id)
        argv = [sys.executable, "-m", "pilot.internal.tasks.wrapper", str(task_dir)]
        launch_id = secrets.token_hex(16)
        read_fd, write_fd = os.pipe()
        process = None
        try:
            process = subprocess.Popen(
                argv,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._environment(task_dir, launch_id, read_fd),
                pass_fds=(read_fd,),
            )
            os.close(read_fd)
            read_fd = -1
            identity = self._inspector.capture(process.pid, argv, launch_id)
            record = TaskProcessRecord(task_id, argv, identity)
            self._store.write_process(task_id, process.pid, record.to_dict())
            os.write(write_fd, b"1")
            return process
        except Exception as error:
            self._abort_start(task_id, process)
            raise TaskProcessStartError(f"Could not start task {task_id}") from error
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            os.close(write_fd)

    def read(self, task_id: str) -> TaskProcessRecord | None:
        data = self._store.read_process(task_id)
        return TaskProcessRecord.from_dict(data) if data is not None else None

    def ownership(self, task_id: str) -> ProcessOwnership:
        try:
            record = self.read(task_id)
            if record is None or record.task_id != task_id:
                return ProcessOwnership.UNKNOWN
            return self._inspector.inspect(record.identity, record.argv)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return ProcessOwnership.UNKNOWN

    def reconcile(self) -> str | None:
        for task_id in self._store.task_ids_with_process():
            ownership = self.ownership(task_id)
            if ownership in {ProcessOwnership.OWNED, ProcessOwnership.UNKNOWN}:
                return task_id
            try:
                status = self._store.read_status(task_id)
            except (OSError, ValueError, TaskNotFoundError):
                return task_id
            if status == TaskStatus.RUNNING:
                self._interrupt(task_id)
            elif status != TaskStatus.QUEUED:
                self._run_stored_callback_for_status(task_id)
                self._store.remove_private_files(
                    task_id,
                    "process.json",
                    "secrets.json",
                    "callbacks.json",
                )
            else:
                return task_id
        self._recover_terminal_callbacks()
        return None

    def cancel(self, task_id: str, grace_seconds: float | None = None) -> None:
        if self._store.read_status(task_id) != TaskStatus.RUNNING:
            raise TaskNotRunningError(f"Task is not running: {task_id}")

        record = self._running_record(task_id)
        ownership = self._inspector.inspect(record.identity, record.argv)
        if self._ownership_prevents_cancel(task_id, ownership):
            return

        outcome = self._signal(record, signal.SIGTERM)
        if self._ownership_prevents_cancel(task_id, outcome, changed=True):
            return

        self._mark_killed(task_id)
        grace = CANCEL_GRACE_SECONDS if grace_seconds is None else grace_seconds
        self._wait_for_exit(record, grace)

    def _running_record(self, task_id: str) -> TaskProcessRecord:
        try:
            record = self.read(task_id)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            record = None
        if record is None or record.task_id != task_id:
            raise TaskNotRunningError(f"Task process ownership is unavailable: {task_id}")
        return record

    def _ownership_prevents_cancel(
        self,
        task_id: str,
        ownership: ProcessOwnership,
        *,
        changed: bool = False,
    ) -> bool:
        if ownership in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
            self._interrupt(task_id)
            return True
        if ownership == ProcessOwnership.UNKNOWN:
            detail = "changed" if changed else "is uncertain"
            raise TaskNotRunningError(f"Task process ownership {detail}: {task_id}")
        return False

    def _mark_killed(self, task_id: str) -> None:
        self._store.remove_private_files(task_id, "secrets.json")
        transitioned = self._store.transition(
            task_id,
            TaskStatus.RUNNING,
            TaskStatus.KILLED,
            {"finished_at": datetime.now(UTC).isoformat()},
        )
        if not transitioned:
            status = self._store.read_status(task_id)
            if not status.is_terminal:
                raise TaskNotRunningError(f"Task state changed during cancellation: {task_id}")

    def _environment(self, task_dir: Path, launch_id: str, read_fd: int) -> dict[str, str]:
        environment = {
            **os.environ,
            _READY_FD_ENV: str(read_fd),
            _LAUNCH_ID_ENV: launch_id,
            NONINTERACTIVE_PRIVILEGES_ENV: "1",
        }
        secret_path = task_dir / "secrets.json"
        if secret_path.exists():
            environment["BENCH_TASK_SECRETS_FILE"] = str(secret_path)
        return environment

    def _abort_start(self, task_id: str, process: subprocess.Popen | None) -> None:
        if process is not None:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
        self._store.remove_private_files(task_id, "process.json", "pid")
        self._interrupt(task_id)

    def _interrupt(self, task_id: str) -> None:
        self._store.transition(
            task_id,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "failure": {"code": "task_interrupted"},
            },
        )
        self._store.remove_private_files(task_id, "process.json", "secrets.json")

    def _wait_for_exit(self, record: TaskProcessRecord, grace_seconds: float) -> None:
        if self._wait_for_process_to_stop(record, grace_seconds):
            return

        outcome = self._signal(record, signal.SIGKILL)
        if outcome in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
            self._clear_process(record.task_id)
            return
        if outcome == ProcessOwnership.UNKNOWN:
            return

        self._wait_for_process_to_stop(record, max(CANCEL_GRACE_SECONDS, grace_seconds))

    def _wait_for_process_to_stop(
        self,
        record: TaskProcessRecord,
        timeout_seconds: float,
    ) -> bool:
        deadline = time.monotonic() + max(0, timeout_seconds)
        while time.monotonic() < deadline:
            ownership = self._inspector.inspect(record.identity, record.argv)
            if ownership in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
                self._clear_process(record.task_id)
                return True
            if ownership == ProcessOwnership.UNKNOWN:
                return True
            time.sleep(_PROCESS_EXIT_POLL_SECONDS)
        return False

    def _run_stored_callback(self, task_id: str, trigger: str) -> None:
        try:
            run_stored_callback(self._store.task_dir(task_id), trigger)
        except Exception as exc:
            logging.debug("Stored callback %r failed for task %s: %s", trigger, task_id, exc)

    def _run_stored_callback_for_status(self, task_id: str) -> None:
        try:
            status = self._store.read_status(task_id)
            trigger = trigger_for_task_status(status)
        except (KeyError, OSError, ValueError, TaskNotFoundError):
            return
        self._run_stored_callback(task_id, trigger)

    def _recover_terminal_callbacks(self) -> None:
        for task_id in self._store.terminal_task_ids_with_callbacks():
            self._run_stored_callback_for_status(task_id)
            self._store.remove_private_files(task_id, "secrets.json", "callbacks.json")

    def _signal(self, record: TaskProcessRecord, signum: signal.Signals) -> ProcessOwnership:
        ownership = self._inspector.inspect(record.identity, record.argv)
        if ownership != ProcessOwnership.OWNED:
            return ownership

        pids = self._inspector.owned_pids(record.identity)
        if not pids:
            return ProcessOwnership.DEAD
        signalled = False
        try:
            for pid in sorted(pids, key=lambda value: value == record.identity.pid):
                signalled = self._signal_owned_pid(record.identity, pid, signum) or signalled
        except PermissionError:
            return ProcessOwnership.UNKNOWN
        return ProcessOwnership.OWNED if signalled else ProcessOwnership.DEAD

    def _signal_owned_pid(
        self,
        identity: ProcessIdentity,
        pid: int,
        signum: signal.Signals,
    ) -> bool:
        pid_descriptor = self._open_pid_descriptor(pid)
        try:
            if not self._inspector.has_pid(identity, pid):
                return False
            if pid_descriptor is not None and hasattr(signal, "pidfd_send_signal"):
                signal.pidfd_send_signal(pid_descriptor, signum)
            else:
                os.kill(pid, signum)
            return True
        except ProcessLookupError:
            return False
        finally:
            if pid_descriptor is not None:
                os.close(pid_descriptor)

    @staticmethod
    def _open_pid_descriptor(pid: int) -> int | None:
        if not hasattr(os, "pidfd_open"):
            return None
        try:
            return os.pidfd_open(pid)
        except OSError:
            return None

    def _clear_process(self, task_id: str) -> None:
        self._store.remove_private_files(task_id, "process.json", "secrets.json")
