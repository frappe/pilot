from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench.job_queue import JobQueueState
    from pilot.core.bench.migration.operation import MigrationOperation

_JOBS_WAIT_SECONDS = 300
_HOLD_WAIT_SECONDS = 60


class BackgroundJobsNotReady(BenchError):
    """A step must not start, because a background job can still run on one of its sites."""

    def __init__(self, message: str, site: str | None = None) -> None:
        super().__init__(message)
        self.site = site


class MigrationBackgroundJobs:
    """Keeps background jobs off the sites of a migration while a step changes them.

    The first backup drains the jobs of every site, then holds new jobs in the queue until the
    operation lifts maintenance mode: a read-only site can still enqueue a job, and the job can
    write after the backup. The operation saves the hold."""

    def __init__(self, operation: MigrationOperation) -> None:
        self.operation = operation
        self.bench = operation.bench

    @property
    def is_workload_running(self) -> bool:
        from pilot.managers.processes.local import ProcessManager

        return ProcessManager.for_bench(self.bench).is_running()

    def check(self, phase: str, names: list[str], on_progress: Callable[[str], None]) -> None:
        if self.operation.is_holding_background_jobs:
            self.wait_until_held(phase)
        else:
            self.drain(names, on_progress)

    def drain(self, names: list[str], on_progress: Callable[[str], None]) -> None:
        """Wait for queued and running jobs while maintenance mode pauses the scheduler."""
        deadline = time.monotonic() + _JOBS_WAIT_SECONDS
        for name in names:
            on_progress(f"Waiting for the background jobs of {name}...")
            try:
                while self.bench.site(name).has_pending_background_jobs:
                    if time.monotonic() >= deadline:
                        raise BenchError(
                            f"{name} still has background jobs after {_JOBS_WAIT_SECONDS} seconds."
                        )
                    time.sleep(2)
            except BenchError as error:
                raise BackgroundJobsNotReady(f"{error} The step did not start. Retry later.", name) from error

    def hold(self, on_progress: Callable[[str], None]) -> None:
        """Suspend the queue, unless someone else did, and restart the workers into the hold."""
        on_progress("Holding new background jobs...")
        operation = self.operation
        is_suspended = self._get_queue_state("backing_up").is_suspended
        # Saved before the suspend, so that a step that stops at any later point still releases the queue.
        operation.is_holding_background_jobs = True
        operation.has_suspended_job_queue = not is_suspended
        self._save()
        if operation.has_suspended_job_queue:
            self.bench.job_queue.suspend()
        # A waiting worker reads the suspend flag only after its next job.
        self.bench.reload_workers(raises=True)
        operation.is_web_restarted_in_maintenance = True
        self._save()
        self.wait_until_held("backing_up")

    def release(self) -> None:
        """End the hold. A queue that was suspended before the hold stays suspended."""
        operation = self.operation
        if not operation.is_holding_background_jobs:
            return
        if operation.has_suspended_job_queue:
            self.bench.job_queue.resume()
        operation.is_holding_background_jobs = False
        operation.has_suspended_job_queue = False
        self._save()

    def wait_until_held(self, phase: str) -> None:
        """The local runner restarts its workers after the request returns, so wait a bit."""
        deadline = time.monotonic() + _HOLD_WAIT_SECONDS
        while True:
            state = self._get_queue_state(phase)
            if state.is_held:
                return
            if not state.is_suspended:
                self._fail_hold(phase, "the job queue is no longer suspended")
            if time.monotonic() >= deadline:
                workers = ", ".join(state.active_workers)
                self._fail_hold(
                    phase,
                    f"these workers still take jobs: {workers}. In lite mode, a job that outlived the "
                    "restart keeps its worker listed until the worker's Redis entry expires",
                )
            time.sleep(1)

    def _get_queue_state(self, phase: str) -> JobQueueState:
        try:
            return self.bench.job_queue.state
        except BenchError as error:
            self._fail_hold(phase, f"the job queue state is unknown: {error}")

    def _fail_hold(self, phase: str, detail: str) -> NoReturn:
        # The queue Redis can be down. The pause still reports why, and the next lift releases again.
        with contextlib.suppress(BenchError):
            self.release()
        if phase == "backing_up":
            result = "The step did not start. Retry later."
        else:
            lost = "Jobs can have written to the sites after the backup, and a revert loses those writes."
            if self.operation.is_holding_background_jobs:
                result = f"{lost} Pilot could not resume the queue, so Retry checks the hold again."
            else:
                result = f"{lost} Retry continues without holding jobs."
        raise BackgroundJobsNotReady(f"Background jobs are not held: {detail}. {result}")

    def _save(self) -> None:
        self.operation.store.save(self.operation)
