from __future__ import annotations

import argparse
import time
from pathlib import Path

from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench


class BaseTask:
    def __init__(self, bench: Bench, bench_root: Path, args: argparse.Namespace) -> None:
        self.bench = bench
        self.bench_root = bench_root
        self._current_step: str | None = None

    def _step(self, key: str, label: str = "") -> None:
        """Open a step fold in the admin UI. Every task emits at least one,
        even single-step ones, so the UI always has a fold to show/collapse."""
        self._current_step = key
        print(f"STEP {key},{time.time():.3f} {label}", flush=True)

    def _step_failed(self) -> None:
        if self._current_step:
            print(f"STEP-FAILED {self._current_step},{time.time():.3f}", flush=True)

    @classmethod
    def _parser(cls) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser()
        p.add_argument("bench_root")
        return p

    @classmethod
    def main(cls) -> None:
        args = cls._parser().parse_args()
        bench_root = Path(args.bench_root)
        bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
        task = cls(bench, bench_root, args)
        try:
            task.run()
        except SystemExit as exit_error:
            if exit_error.code not in (0, None):
                task._step_failed()
            raise
        except Exception:
            task._step_failed()
            raise

    def run(self) -> None:
        raise NotImplementedError
