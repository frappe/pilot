from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

from pilot.internal.tasks.process import TaskProcess, TaskProcessStartError
from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStatus, WorkerStore

_CONTROL_POLL_SECONDS = 0.2


class TaskWorker:
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
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def request_drain(self) -> None:
        with self._claim_lock:
            self._drain.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        lock = self._worker.try_acquire()
        if lock is None:
            return

        pid = os.getpid()
        with lock:
            self._worker.write_pid(pid)
            self._write_state(WorkerStatus.STARTING, pid)
            try:
                self._work(pid)
            finally:
                self._write_state(WorkerStatus.STOPPED, None)
                self._worker.write_pid(None)

    def _work(self, pid: int) -> None:
        while not self._drain.is_set():
            self._wake.clear()
            blocking_task = self._processes.reconcile()
            if blocking_task is not None:
                status = WorkerStatus.DRAINING if self._intent_stopped() else WorkerStatus.RUNNING
                self._write_state(status, pid, blocking_task)
                self._wake.wait(_CONTROL_POLL_SECONDS)
                continue
            if self._intent_stopped():
                self._write_state(WorkerStatus.STOPPED, pid)
                self._wake.wait(_CONTROL_POLL_SECONDS)
                continue
            if self._run_next(pid):
                continue
            if self._drain.is_set():
                break
            if self._intent_stopped():
                continue
            self._write_state(WorkerStatus.IDLE, pid)
            self._wake.wait(_CONTROL_POLL_SECONDS)

    def _run_next(self, pid: int) -> bool:
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
            raise RuntimeError(f"Task wrapper exited without finalizing {task_id}")
        return True

    def _wait_for_task(
        self,
        process: subprocess.Popen,
        pid: int,
        task_id: str,
    ) -> None:
        draining = False
        while True:
            try:
                process.wait(timeout=0.1)
                return
            except subprocess.TimeoutExpired:
                if self._should_drain() and not draining:
                    self._write_state(WorkerStatus.DRAINING, pid, task_id)
                    draining = True

    def _claim_next(self) -> str | None:
        with self._claim_lock:
            if self._drain.is_set():
                return None
            with self._worker.locked_intent() as intent:
                if intent == WorkerIntent.STOPPED:
                    return None
                return self._queue.claim_next()

    def _should_drain(self) -> bool:
        return self._drain.is_set() or self._intent_stopped()

    def _intent_stopped(self) -> bool:
        return self._worker.read_intent() == WorkerIntent.STOPPED

    def _write_state(
        self,
        status: WorkerStatus,
        pid: int | None,
        task_id: str | None = None,
    ) -> None:
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
        with self._lock:
            worker = self._workers.get(Path(bench_root).resolve())
        if worker is None or not worker.is_alive():
            return False
        worker.wake()
        return True

    def request_drain(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.request_drain()

    def install_signal_handlers(self) -> None:
        if self._signals_installed:
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(signum)

            def drain(signum, frame, previous=previous):
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
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join()
        os.kill(os.getpid(), signum)


task_workers = WorkerRegistry()
