#!/usr/bin/env python3
"""Run the Pilot performance benchmark harness.

Usage:
    uv run python scripts/benchmark/run.py
    uv run python scripts/benchmark/run.py --group config --iterations 50
    uv run python scripts/benchmark/run.py --profile --md results/run.md
    uv run python scripts/benchmark/run.py --compare results/baseline.json
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark import (  # noqa: E402
    __version__,
    registry,
    report,
)
from benchmark.fixtures import Fixture, build_fixture  # noqa: E402
from benchmark.harness import (  # noqa: E402
    BenchmarkCase,
    CaseResult,
    Runner,
    results_payload,
    to_baseline,
    write_json,
)
from benchmark.install import make_install_root  # noqa: E402
from benchmark.patches import apply_patches  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pilot performance benchmark harness")
    parser.add_argument("--list", action="store_true", help="List available cases and exit")
    parser.add_argument("--group", help="Run only this case group")
    parser.add_argument("--case", help="Run only this case (exact name)")
    parser.add_argument("--iterations", type=int, help="Override iterations for every selected case")
    parser.add_argument("--warmup", type=int, help="Override warmup runs for every selected case")
    parser.add_argument("--profile", action="store_true", help="Profile cases marked profile=True")
    parser.add_argument("--profile-all", action="store_true", help="Profile every selected case")
    parser.add_argument("--profile-iterations", type=int, default=3, help="Runs per profile")
    parser.add_argument("--json", type=Path, help="Write full JSON results to this path")
    parser.add_argument("--md", type=Path, help="Write Markdown report to this path")
    parser.add_argument(
        "--compare",
        "--baseline",
        dest="compare",
        type=Path,
        help="Compare against a baseline JSON written by --save-baseline",
    )
    parser.add_argument("--save-baseline", type=Path, help="Save medians as a baseline JSON")
    parser.add_argument("--root", type=Path, help="Use this directory instead of a temp dir (not cleaned)")
    parser.add_argument("--keep", action="store_true", help="Keep the temp directory after the run")
    parser.add_argument("--apps", type=int, default=3, help="Number of apps in the fixture")
    parser.add_argument("--sites", type=int, default=3, help="Number of sites in the fixture")
    parser.add_argument("--tasks", type=int, default=12, help="Number of tasks in the fixture")
    parser.add_argument("--log-lines", type=int, default=2000, help="Lines per seeded log file")
    parser.add_argument("--audit-entries", type=int, default=300, help="Audit log records")
    parser.add_argument("--quiet", action="store_true", help="Only print the final summary")
    parser.add_argument("--version", action="version", version=f"benchmark {__version__}")
    return parser.parse_args(argv)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            cwd=SCRIPTS_DIR.parent,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _default_results_dir() -> Path:
    return Path(__file__).resolve().parent / "results"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cases = registry.select_cases(group=args.group, name=args.case)
    if args.list:
        for case in cases:
            print(f"{case.group:<12} {case.name:<28} {case.description}")
        return 0
    if not cases:
        print("No cases matched.", file=sys.stderr)
        return 2

    root, temporary = _resolve_root(args)
    fixture = _build_fixture(root, args)
    apply_patches()
    runner = _make_runner(fixture, args)
    selected = _select_for_profile(cases, args)

    if not args.quiet:
        print(
            f"Fixture: {len(fixture.app_names)} apps, {len(fixture.site_names)} sites, "
            f"{fixture.task_count} tasks, {fixture.log_lines} log lines"
        )
        print(f"Running {len(selected)} case(s) in {root}")
    started = time.monotonic()
    results = _run_cases(runner, selected, quiet=args.quiet)
    elapsed = time.monotonic() - started

    baseline = _load_baseline(args.compare)
    meta = _build_meta(args, fixture, elapsed)
    json_path, md_path = _write_outputs(args, results, meta, baseline)

    print()
    print(report.render_terminal_summary(results, baseline))
    print()
    print(f"JSON: {json_path}")
    print(f"Report: {md_path}")

    if temporary is not None:
        if args.keep:
            print(f"Kept temp directory: {temporary}")
        else:
            shutil.rmtree(temporary, ignore_errors=True)
    return 0


def _resolve_root(args) -> tuple[Path, Path | None]:
    if args.root is not None:
        return args.root, None
    base = os.environ.get("PILOT_BENCHMARK_ROOT", "/tmp/opencode")
    Path(base).mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="pilot-benchmark-", dir=base))
    return temporary, temporary


def _build_fixture(root: Path, args) -> Fixture:
    return build_fixture(
        make_install_root(root / "install"),
        app_count=args.apps,
        site_count=args.sites,
        task_count=args.tasks,
        log_lines=args.log_lines,
        audit_entries=args.audit_entries,
    )


def _make_runner(fixture: Fixture, args) -> Runner:
    profile_dir = Path(__file__).resolve().parent / "results" / "profiles"
    return Runner(
        fixture,
        iterations=args.iterations,
        warmup=args.warmup,
        profile_iterations=args.profile_iterations,
        profile_dir=profile_dir,
        profile_enabled=args.profile or args.profile_all,
    )


def _select_for_profile(cases: list[BenchmarkCase], args) -> list[BenchmarkCase]:
    if args.profile_all:
        return [_with_profile(case) for case in cases]
    return list(cases)


def _run_cases(runner: Runner, selected: list[BenchmarkCase], *, quiet: bool) -> list[CaseResult]:
    results = []
    for case in selected:
        if not quiet:
            print(f"  {case.name}...", end=" ", flush=True)
        result = runner.run(case)
        results.append(result)
        if not quiet:
            _report_case(result)
    return results


def _report_case(result: CaseResult) -> None:
    if result.error:
        print(f"ERROR: {result.error}")
        return
    measurement = result.measurements[0]
    print(f"{measurement.median_ms:.2f}ms median ({len(measurement.samples_ms)} runs)")


def _load_baseline(path) -> dict | None:
    if path is None:
        return None
    from benchmark.harness import load_baseline

    return load_baseline(path)


def _build_meta(args, fixture: Fixture, elapsed: float) -> dict:
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "git_sha": _git_sha(),
        "elapsed_seconds": round(elapsed, 2),
        "fixture": {
            "apps": len(fixture.app_names),
            "sites": len(fixture.site_names),
            "tasks": fixture.task_count,
            "log_lines": fixture.log_lines,
            "audit_entries": args.audit_entries,
        },
    }


def _write_outputs(args, results: list[CaseResult], meta: dict, baseline: dict | None) -> tuple[Path, Path]:
    json_path = args.json or _default_results_dir() / "latest.json"
    md_path = args.md or _default_results_dir() / "latest.md"
    write_json(json_path, results_payload(results, meta))
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.render_markdown(results, meta, baseline))
    if args.save_baseline:
        write_json(args.save_baseline, to_baseline(results))
    return json_path, md_path


def _with_profile(case):
    from dataclasses import replace

    return replace(case, profile=True)


if __name__ == "__main__":
    raise SystemExit(main())
