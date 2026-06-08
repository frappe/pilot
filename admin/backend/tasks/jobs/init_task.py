from __future__ import annotations

import argparse
from pathlib import Path

from bench_cli.commands.init import InitCommand
from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bench init as a task")
    parser.add_argument("bench_root")
    parser.add_argument("--sudo-password", default="")
    args = parser.parse_args()

    bench_root = Path(args.bench_root)
    config = BenchConfig.from_file(bench_root / "bench.toml")
    bench = Bench(config, bench_root)
    InitCommand(bench, sudo_password=args.sudo_password).run()


if __name__ == "__main__":
    main()
