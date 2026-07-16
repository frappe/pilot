from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

from pilot.tasks.manager.worker import TaskWorker


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
