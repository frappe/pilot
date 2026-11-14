from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from pilot.internal.tasks.process import TaskProcess, TaskProcessStartError
from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStatus, WorkerStore


class TaskWorker:
    database_poll_seconds = 1.0

    control_poll_seconds = 0.2

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = Path(bench_root)
        self._queue = TaskQueue(self._bench_root)
        self._processes = TaskProcess(self._bench_root)
        self._tasks = TaskStore(self._bench_root)
        self._worker = WorkerStore(self._bench_root)
        self._wake = threading.Event()
        self._drain = threading.Event()
        self._claim_lock = threading.Lock()
        self._last_state: tuple[WorkerStatus, int | None, str | None] | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="bench-task-worker",
        )

    def start(self) -> None:
        """Start the worker thread."""
        self._thread.start()

    def wake(self) -> None:
        """Wake the worker to look for queued tasks now."""
        self._wake.set()

    def request_drain(self) -> None:
        """Stop claiming new tasks and let the running one finish."""
        with self._claim_lock:
            self._drain.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker thread to end."""
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        """Whether the worker thread is running."""
        return self._thread.is_alive()

    def _run(self) -> None:
        """The worker loop: hold the bench worker lock and run queued tasks until drained."""
        lock = self._worker.try_acquire()
        if lock is None:
            return

        pid = os.getpid()
        with lock:
            self._worker.write_pid(pid)
            self._write_state(WorkerStatus.STARTING, pid)
            try:
                self._wait_for_database()
                self._work(pid)
            finally:
                self._write_state(WorkerStatus.STOPPED, None)
                self._worker.write_pid(None)

    def _wait_for_database(self) -> None:
        """Hold the first task until the bench's database is up, for at most the timeout."""
        from pilot.core.bench import Bench
        from pilot.core.bench.runtime import BenchRuntime
        from pilot.exceptions import BenchError

        deadline = time.monotonic() + 120.0
        try:
            bench = Bench(self._bench_root)
            if not bench.is_initialized:
                return
            runtime = BenchRuntime(bench)
            while not runtime.is_database_ready:
                if time.monotonic() >= deadline:
                    logging.warning("The database is not reachable; queued tasks run anyway.")
                    return
                if self._drain.wait(self.database_poll_seconds):
                    return
        except (BenchError, OSError) as error:
            logging.warning("Could not check the database before running tasks: %s", error)

    def _work(self, pid: int) -> None:
        """Survive a failed cycle instead of ending the thread."""
        while not self._drain.is_set():
            try:
                self._work_once(pid)
            except Exception:
                logging.exception("Task worker cycle failed")
                self._wake.wait(1.0)

    def _work_once(self, pid: int) -> None:
        """One pass: reconcile processes, then run the next task if nothing blocks."""
        self._wake.clear()
        blocking_task = self._processes.reconcile()
        if blocking_task is not None:
            status = WorkerStatus.DRAINING if self._intent_stopped() else WorkerStatus.RUNNING
            self._write_state(status, pid, blocking_task)
            self._wake.wait(self.control_poll_seconds)
            return
        if self._intent_stopped():
            self._write_state(WorkerStatus.STOPPED, pid)
            self._wake.wait(self.control_poll_seconds)
            return
        if self._run_next(pid):
            return
        if self._drain.is_set() or self._intent_stopped():
            return
        self._write_state(WorkerStatus.IDLE, pid)
        self._wake.wait(self.control_poll_seconds)

    def _run_next(self, pid: int) -> bool:
        """Claim and run the next queued task; False when none is queued."""
        task_id = self._claim_next()
        if task_id is None:
            return False

        self._write_state(WorkerStatus.RUNNING, pid, task_id)
        try:
            process = self._processes.start(task_id)
        except TaskProcessStartError:
            return True
        self._wait_for_task(process, pid, task_id)
        if not self._tasks.read_status(task_id).is_terminal:
            logging.error("Task wrapper exited without finalizing %s", task_id)
        self._processes.interrupt(task_id)
        return True

    def _wait_for_task(
        self,
        process: subprocess.Popen,
        pid: int,
        task_id: str,
    ) -> None:
        """Wait for the task process to end, recording a drain request while it runs."""
        draining = False
        while True:
            try:
                process.wait(timeout=0.1)
                return
            except subprocess.TimeoutExpired:
                self._processes.enforce_timeout(task_id)
                if self._should_drain() and not draining:
                    self._write_state(WorkerStatus.DRAINING, pid, task_id)
                    draining = True

    def _claim_next(self) -> str | None:
        """The next queued task id, claimed for running; None when draining or stopped."""
        with self._claim_lock:
            if self._drain.is_set():
                return None
            with self._worker.locked_intent() as intent:
                if intent == WorkerIntent.STOPPED:
                    return None
                return self._queue.claim_next()

    def _should_drain(self) -> bool:
        """Whether the worker should stop claiming tasks."""
        return self._drain.is_set() or self._intent_stopped()

    def _intent_stopped(self) -> bool:
        """Whether the stored worker intent is stopped."""
        return self._worker.read_intent() == WorkerIntent.STOPPED

    def _write_state(
        self,
        status: WorkerStatus,
        pid: int | None,
        task_id: str | None = None,
    ) -> None:
        """Write the worker state, skipping a write when nothing changed."""
        state = (status, pid, task_id)
        if state == self._last_state:
            return
        self._worker.write_state(status, pid, task_id)
        self._last_state = state


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[Path, TaskWorker] = {}
        self._lock = threading.Lock()
        self._signals_installed = False

    def start(self, bench_root: Path) -> TaskWorker:
        """The bench's worker, started if it is not already running."""
        key = Path(bench_root).resolve()
        with self._lock:
            current = self._workers.get(key)
            if current is not None and current.is_alive():
                return current
            worker = TaskWorker(key)
            self._workers[key] = worker
            worker.start()
            return worker

    def wake(self, bench_root: Path) -> bool:
        """Restart this process's worker first if its thread died."""
        key = Path(bench_root).resolve()
        with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                return False
            if worker.is_alive():
                worker.wake()
                return True
        return self.start(key).is_alive()

    def request_drain(self) -> None:
        """Ask every worker to drain."""
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.request_drain()

    def install_signal_handlers(self) -> None:
        """Drain workers on SIGTERM and SIGINT, then pass the signal on."""
        if self._signals_installed:
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(signum)

            def drain(signum, frame, previous=previous):
                """Drain the workers, then run the earlier handler or the default action."""
                self.request_drain()
                if callable(previous):
                    previous(signum, frame)
                elif previous == signal.SIG_DFL:
                    signal.signal(signum, signal.SIG_DFL)
                    threading.Thread(
                        target=self._terminate_after_drain,
                        args=(signum,),
                        daemon=True,
                    ).start()

            signal.signal(signum, drain)
        self._signals_installed = True

    def _terminate_after_drain(self, signum: int) -> None:
        """Wait for every worker, then send the signal to this process again."""
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join()
        os.kill(os.getpid(), signum)


task_workers = WorkerRegistry()
