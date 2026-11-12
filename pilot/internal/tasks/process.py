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
from pilot.internal.tasks.callbacks import BoundedCallback, CallbackOperations
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.process_identity import (
    ProcessIdentity,
    ProcessInspector,
    ProcessOwnership,
    TaskProcessRecord,
)
from pilot.internal.tasks.store import TaskStore
from pilot.managers.platform import Privileges

_READY_FD_ENV = "BENCH_TASK_READY_FD"
_LAUNCH_ID_ENV = "BENCH_TASK_LAUNCH_ID"
# Lets a running task hand its resources to the task it queues next.
TASK_ID_ENV = "BENCH_TASK_ID"
WRAPPER_MODULE = "pilot.internal.tasks.wrapper"
CANCEL_GRACE_SECONDS = 3.0
_PROCESS_EXIT_POLL_SECONDS = 0.05
_UNKNOWN_GRACE_SECONDS = 60.0


class TaskProcessStartError(BenchError):
    pass


class TaskProcess:
    def __init__(self, bench_root: Path) -> None:
        self._store = TaskStore(bench_root)
        self._inspector = ProcessInspector()
        self._unknown_since: dict[str, float] = {}
        self._deadlines: dict[str, float] = {}
        self._expired: set[str] = set()

    def start(self, task_id: str) -> subprocess.Popen:
        """Start the task wrapper in its own process group and record its identity."""
        task_dir = self._store.task_dir(task_id)
        argv = [sys.executable, "-m", WRAPPER_MODULE, str(task_dir)]
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
        """The task's recorded process, or None when none is recorded."""
        data = self._store.read_process(task_id)
        return TaskProcessRecord.from_dict(data) if data is not None else None

    def ownership(self, task_id: str) -> ProcessOwnership:
        """Whether the task's recorded process is still ours; UNKNOWN when it cannot be read."""
        try:
            record = self.read(task_id)
            if record is None or record.task_id != task_id:
                return ProcessOwnership.UNKNOWN
            return self._inspector.inspect(record.identity, record.argv)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return ProcessOwnership.UNKNOWN

    def reconcile(self) -> str | None:
        """Settle every recorded process and return the id of a task that still blocks the queue, if any."""
        # The wrapper waits until its process record exists, so a running task without one
        # never started: its worker stopped between the claim and the start.
        for task_id in self._store.running_task_ids_without_process():
            self.interrupt(task_id)
        for task_id in self._store.task_ids_with_process():
            self.enforce_timeout(task_id)
            ownership = self._reconciled_ownership(task_id)
            if ownership in {ProcessOwnership.OWNED, ProcessOwnership.UNKNOWN}:
                return task_id
            try:
                status = self._store.read_status(task_id)
            except (OSError, ValueError, TaskNotFoundError):
                return task_id
            if status == TaskStatus.RUNNING:
                self.interrupt(task_id)
            elif status == TaskStatus.QUEUED or not self._recover_finished_task(task_id):
                return task_id
        return self._recover_terminal_callbacks()

    def _reconciled_ownership(self, task_id: str) -> ProcessOwnership:
        """The task's ownership after resolving uncertain and finished cases."""
        ownership = self.ownership(task_id)
        if ownership == ProcessOwnership.UNKNOWN:
            return self._resolve_unknown(task_id)
        if ownership == ProcessOwnership.OWNED:
            try:
                record = self._running_record(task_id)
            except TaskNotRunningError:
                return ProcessOwnership.UNKNOWN
            if record.is_callback or self._inspector.is_leader_dead(record.identity):
                self.interrupt(task_id)
        return ownership

    def _resolve_unknown(self, task_id: str) -> ProcessOwnership:
        """Look for the wrapper or replay callback when its record is unreadable."""
        task_dir = str(self._store.task_dir(task_id))
        try:
            for module in (WRAPPER_MODULE, "pilot.internal.tasks.callbacks"):
                if self._inspector.get_pid_matching([module, task_dir], self._pid_hint(task_id)) is not None:
                    self._unknown_since.pop(task_id, None)
                    return ProcessOwnership.OWNED
        except OSError:
            return self._unknown_within_grace(task_id)
        self._unknown_since.pop(task_id, None)
        return ProcessOwnership.DEAD

    def _unknown_within_grace(self, task_id: str) -> ProcessOwnership:
        """UNKNOWN for a short grace period after it is first seen, then DEAD."""
        # ponytail: in-memory clock, a worker restart extends the wait
        first_seen = self._unknown_since.setdefault(task_id, time.monotonic())
        if time.monotonic() - first_seen < _UNKNOWN_GRACE_SECONDS:
            return ProcessOwnership.UNKNOWN
        self._unknown_since.pop(task_id, None)
        return ProcessOwnership.DEAD

    def _pid_hint(self, task_id: str) -> int | None:
        """The task's recorded pid, or None when it cannot be read."""
        try:
            return self._store.read_pid(task_id)
        except (OSError, ValueError, TaskNotFoundError):
            return None

    def cancel(self, task_id: str, grace_seconds: float | None = None) -> None:
        """Stop a running task: signal its group, wait for the grace period, then kill it."""
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
        """The running task's process record; raise when it is missing or belongs to another task."""
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
        """Whether the process state means cancel must stop here, interrupting a dead task first."""
        if ownership in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
            self.interrupt(task_id)
            return True
        if ownership == ProcessOwnership.UNKNOWN:
            detail = "changed" if changed else "is uncertain"
            raise TaskNotRunningError(f"Task process ownership {detail}: {task_id}")
        return False

    def _mark_killed(self, task_id: str) -> None:
        """Mark a running task as killed and drop its secrets."""
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
        """The environment for the task wrapper, with its launch id and ready pipe."""
        environment = {
            **os.environ,
            _READY_FD_ENV: str(read_fd),
            _LAUNCH_ID_ENV: launch_id,
            TASK_ID_ENV: task_dir.name,
            Privileges.environment_key: "1",
        }
        secret_path = task_dir / "secrets.json"
        if secret_path.exists():
            environment["BENCH_TASK_SECRETS_FILE"] = str(secret_path)
        return environment

    def _abort_start(self, task_id: str, process: subprocess.Popen | None) -> None:
        """Kill a half-started wrapper and remove its process files."""
        if process is not None:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
        self._store.remove_private_files(task_id, "process.json", "pid")
        self.interrupt(task_id)

    def enforce_timeout(self, task_id: str) -> None:
        """Kill the task once it runs past its time budget."""
        if self._store.read_status(task_id) != TaskStatus.RUNNING:
            self._deadlines.pop(task_id, None)
            self._expired.discard(task_id)
            return
        if task_id not in self._deadlines:
            try:
                self._deadlines[task_id] = time.monotonic() + self._remaining_budget(task_id)
            except (OSError, TypeError, ValueError, TaskNotFoundError):
                logging.exception("Cannot restore the execution deadline for task %s", task_id)
                return
        if time.monotonic() >= self._deadlines[task_id]:
            if task_id not in self._expired:
                logging.error("Task %s exceeded its execution time limit", task_id)
            self._expired.add(task_id)
            self.interrupt(task_id)

    def _remaining_budget(self, task_id: str) -> float:
        """The seconds the task has left before its timeout."""
        meta = self._store.read_metadata(task_id)
        timeout = meta.get("timeout_seconds", 24 * 60 * 60)
        started_at = meta.get("started_at")
        elapsed = (
            (datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds() if started_at else 0
        )
        return max(0, timeout - max(0, elapsed))

    def interrupt(self, task_id: str) -> None:
        """Fail a task whose process died without finalizing."""
        try:
            record = self.read(task_id)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, TaskNotFoundError):
            record = None
        if record is None and task_id in self._expired:
            return
        if record is not None and self._stop_interrupted_processes(record) == ProcessOwnership.UNKNOWN:
            return
        self._store.transition(
            task_id,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "failure": {"code": "task_timeout" if task_id in self._expired else "task_interrupted"},
            },
        )
        self._store.remove_private_files(task_id, "process.json", "secrets.json")
        self._deadlines.pop(task_id, None)
        self._expired.discard(task_id)

    def _stop_interrupted_processes(self, record: TaskProcessRecord) -> ProcessOwnership:
        """Kill what is left of an interrupted task; UNKNOWN when some processes may survive."""
        ownership = self._inspector.inspect(record.identity, record.argv)
        if ownership == ProcessOwnership.OWNED:
            outcome = self._signal(record, signal.SIGKILL)
            if outcome == ProcessOwnership.UNKNOWN or self._inspector.owned_pids(record.identity):
                return ProcessOwnership.UNKNOWN
        return ownership

    def _wait_for_exit(self, record: TaskProcessRecord, grace_seconds: float) -> None:
        """Wait for the group to exit within the grace period, then kill it."""
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
        """True once nothing the task launched is left running. A descendant that
        ignores SIGTERM outlives the leader, and it still has to be killed."""
        deadline = time.monotonic() + max(0, timeout_seconds)
        cleared = False
        # UNKNOWN is not an exit: /proc reads fail briefly while a process dies.
        while time.monotonic() < deadline:
            ownership = self._inspector.inspect(record.identity, record.argv)
            if ownership in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
                if not cleared:
                    self._clear_process(record.task_id)
                    cleared = True
                if not self._inspector.owned_pids(record.identity):
                    return True
            time.sleep(_PROCESS_EXIT_POLL_SECONDS)
        return False

    def _run_stored_callback(self, task_id: str, trigger: str) -> None:
        """Run the task's stored callback for a trigger, logging a failure to the task output."""
        try:
            BoundedCallback.run_stored(self._store.task_dir(task_id), trigger)
        except Exception as exc:
            from pilot.internal.command import Command
            from pilot.internal.private_files import PrivateFile
            from pilot.internal.tasks.wrapper import _load_redactions

            message = Command.redact(
                str(exc), _load_redactions(self._store.task_dir(task_id), self._store.bench_root)
            )
            logging.warning("Stored callback %r failed for task %s: %s", trigger, task_id, message)
            with PrivateFile(self._store.task_dir(task_id) / "output.log").open("a") as output:
                output.write(f"Recovery callback failed: {message}\n")

    def _run_stored_callback_for_status(self, task_id: str) -> None:
        """Run the stored callback that matches the task's final status."""
        try:
            status = self._store.read_status(task_id)
            trigger = CallbackOperations.get_trigger(status)
        except (KeyError, OSError, ValueError, TaskNotFoundError):
            return
        self._run_stored_callback(task_id, trigger)

    def _recover_terminal_callbacks(self) -> str | None:
        """Finish callbacks for finished tasks; return a task that still has a live process."""
        for task_id in self._store.terminal_task_ids_with_callbacks():
            if not self._recover_finished_task(task_id):
                return task_id
        return None

    def _recover_finished_task(self, task_id: str) -> bool:
        """Run the finished task's callback and clear its files; False while its process lives."""
        self._run_stored_callback_for_status(task_id)
        if self._reconciled_ownership(task_id) in {ProcessOwnership.OWNED, ProcessOwnership.UNKNOWN}:
            return False
        self._store.remove_private_files(task_id, "process.json", "secrets.json", "callbacks.json")
        return True

    def _signal(
        self, record: TaskProcessRecord, signum: signal.Signals, *, exclude_pids: set[int] | None = None
    ) -> ProcessOwnership:
        """Send a signal to every process in the task's group that carries its launch id."""
        ownership = self._inspector.inspect(record.identity, record.argv)
        if ownership == ProcessOwnership.UNKNOWN:
            return ownership

        # The launch id, not the leader pid, is what proves the group is ours, so
        # survivors are still signalled once the leader itself is gone.
        pids = self._inspector.owned_pids(record.identity) - (exclude_pids or set())
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
        """Signal one pid only if it still belongs to the identity; prefer a pidfd to avoid pid reuse."""
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
        """A pidfd for the process, or None where the platform has none."""
        if not hasattr(os, "pidfd_open"):
            return None
        try:
            return os.pidfd_open(pid)
        except OSError:
            return None

    def _clear_process(self, task_id: str) -> None:
        """Remove the task's process record and secrets."""
        self._store.remove_private_files(task_id, "process.json", "secrets.json")
