"""End-to-end CLI benchmarks: interpreter startup plus command dispatch."""

from __future__ import annotations

import shutil
import subprocess
import sys

from benchmark.fixtures import Fixture
from benchmark.harness import Case


def _run_cli(fixture: Fixture, arguments: list[str], timeout: float = 120) -> None:
    argv = [sys.executable, str(fixture.root / "bin" / "pilot"), *arguments]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")


def _remove_new_bench(fixture: Fixture) -> None:
    shutil.rmtree(fixture.benches_root / "benchmark-new", ignore_errors=True)


CASES = [
    Case(
        name="cli.version",
        group="cli",
        description="pilot --version: interpreter startup and package import",
        fn=lambda fixture: _run_cli(fixture, ["--version"]),
        iterations=30,
        warmup=3,
        profile=False,
    ),
    Case(
        name="cli.help",
        group="cli",
        description="pilot --help: command discovery over every command module",
        fn=lambda fixture: _run_cli(fixture, ["--help"]),
        iterations=30,
        warmup=3,
    ),
    Case(
        name="cli.ls",
        group="cli",
        description="pilot ls: describe every bench (config + process state)",
        fn=lambda fixture: _run_cli(fixture, ["ls"]),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="cli.list_apps",
        group="cli",
        description="pilot -b bench list-apps: git-backed app inventory",
        fn=lambda fixture: _run_cli(fixture, ["-b", "bench", "list-apps"]),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="cli.list_site_apps",
        group="cli",
        description="pilot -b bench list-site-apps: site apps from config and DB",
        fn=lambda fixture: _run_cli(fixture, ["-b", "bench", "list-site-apps", "site1.localhost"]),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="cli.processes_ls",
        group="cli",
        description="pilot -b bench processes ls: definitions and process state",
        fn=lambda fixture: _run_cli(fixture, ["-b", "bench", "processes", "ls"]),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="cli.tasks_status",
        group="cli",
        description="pilot -b bench tasks status: worker state and task counts",
        fn=lambda fixture: _run_cli(fixture, ["-b", "bench", "tasks", "status"]),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="cli.new",
        group="cli",
        description="pilot new: config build, port probe, password hash, bench.toml write",
        fn=lambda fixture: _run_cli(fixture, ["new", "benchmark-new"]),
        setup=lambda fixture: _remove_new_bench(fixture),
        iterations=5,
        warmup=1,
        profile=True,
    ),
]
