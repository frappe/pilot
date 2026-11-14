from __future__ import annotations

from pathlib import Path

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStore


class TaskWorkerControl:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = Path(bench_root)
        self._store = WorkerStore(self._bench_root)

    def request_start(self) -> None:
        """Ask the worker to run, and wake it."""
        self._store.write_intent(WorkerIntent.RUNNING)
        self.wake()

    def request_stop(self) -> None:
        """Ask the worker to stop, and wake it."""
        self._store.write_intent(WorkerIntent.STOPPED)
        worker_module.task_workers.wake(self._bench_root)

    def wake(self) -> None:
        """Wake this process's worker. Without one, start the Admin, which runs the worker."""
        if worker_module.task_workers.wake(self._bench_root):
            return

        from pilot.core.bench import Bench
        from pilot.managers.processes.local import ProcessManager

        ProcessManager.for_bench(Bench(self._bench_root)).wake_admin()

    def start_background_worker(self) -> None:
        """Start the worker in this process."""
        worker_module.task_workers.start(self._bench_root)
        worker_module.task_workers.install_signal_handlers()
