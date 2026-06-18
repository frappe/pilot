"""Provision a freshly-created bench to production, unattended.

Spawned detached by ``/api/benches/new`` when the bench is created from a
production admin: a new production bench should come up the same way its parent
did (process manager + nginx + TLS), not drop the user at a raw-port wizard.

Runs ``bench init`` then ``bench setup production`` against the new bench,
recording coarse progress in ``provision.status`` (running/done/failed) and full
output in ``provision.log`` so the dialog can poll and then redirect to the
bench's own domain.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path


def _write_status(bench_root: Path, status: str) -> None:
    (bench_root / "provision.status").write_text(status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a new bench to production")
    parser.add_argument("bench_root")
    args = parser.parse_args()

    bench_root = Path(args.bench_root)
    log = open(bench_root / "provision.log", "a", buffering=1)
    sys.stdout = log
    sys.stderr = log

    # Import lazily so a syntax/import error still lands in the log file.
    from bench_cli.commands.init import InitCommand
    from bench_cli.commands.setup.production import SetupProductionCommand
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench

    _write_status(bench_root, "running")
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
        bench = Bench(config, bench_root)

        print("=== bench init ===", flush=True)
        InitCommand(bench).run()

        # Re-read: init may rewrite bench.toml (e.g. resolved versions).
        config = BenchConfig.from_file(bench_root / "bench.toml")
        bench = Bench(config, bench_root)

        print("\n=== bench setup production ===", flush=True)
        SetupProductionCommand(bench).run()

        _write_status(bench_root, "done")
        print("\n=== provisioning complete ===", flush=True)
    except Exception:
        traceback.print_exc()
        _write_status(bench_root, "failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
