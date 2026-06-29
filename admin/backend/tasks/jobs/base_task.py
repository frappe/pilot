from __future__ import annotations

import argparse
from pathlib import Path

from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench


class BaseTask:
    def __init__(self, bench: Bench, bench_root: Path, args: argparse.Namespace) -> None:
        self.bench = bench
        self.bench_root = bench_root

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
        cls(bench, bench_root, args).run()

    def run(self) -> None:
        raise NotImplementedError
