from __future__ import annotations

from pathlib import Path

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStore


class TaskWorkerControl:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = Path(bench_root)
        self._store = WorkerStore(self._bench_root)

    def request_start(self) -> None:
        self._store.write_intent(WorkerIntent.RUNNING)
        worker_module.task_workers.wake(self._bench_root)

    def request_stop(self) -> None:
        self._store.write_intent(WorkerIntent.STOPPED)
        worker_module.task_workers.wake(self._bench_root)

    def start_background_worker(self) -> None:
        worker_module.task_workers.start(self._bench_root)
        worker_module.task_workers.install_signal_handlers()
