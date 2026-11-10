"""Benchmark engine: case model, timing, cProfile, and result aggregation.

The harness never imports Pilot's CLI entry point. Cases receive a built
`Fixture` and call Pilot objects directly, so a case measures the code path
rather than interpreter startup. Subprocess cases live in `cases_cli.py`.
"""

from __future__ import annotations

import cProfile
import io
import json
import math
import pstats
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from benchmark.fixtures import Fixture


@dataclass(frozen=True)
class Step:
    """One timed step inside a `StepCase`."""

    label: str
    fn: Callable[[Fixture], None]


@dataclass(frozen=True)
class Case:
    """A single timed operation, repeated `iterations` times."""

    name: str
    group: str
    description: str
    fn: Callable[[Fixture], None]
    setup: Callable[[Fixture], None] | None = None
    iterations: int = 10
    warmup: int = 2
    profile: bool = False


@dataclass(frozen=True)
class StepCase:
    """A workflow whose steps are timed individually."""

    name: str
    group: str
    description: str
    steps: tuple[Step, ...]
    setup: Callable[[Fixture], None] | None = None
    iterations: int = 5
    warmup: int = 1
    profile: bool = False


BenchmarkCase = Case | StepCase


@dataclass
class Measurement:
    label: str
    samples_ms: list[float] = field(default_factory=list)
    cpu_ms: list[float] = field(default_factory=list)

    @property
    def min_ms(self) -> float:
        return min(self.samples_ms) if self.samples_ms else 0.0

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples_ms) if self.samples_ms else 0.0

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.samples_ms) if self.samples_ms else 0.0

    @property
    def p90_ms(self) -> float:
        if not self.samples_ms:
            return 0.0
        ordered = sorted(self.samples_ms)
        index = max(0, math.ceil(0.9 * len(ordered)) - 1)
        return ordered[index]

    @property
    def max_ms(self) -> float:
        return max(self.samples_ms) if self.samples_ms else 0.0

    @property
    def stdev_ms(self) -> float:
        return statistics.stdev(self.samples_ms) if len(self.samples_ms) > 1 else 0.0

    @property
    def cpu_median_ms(self) -> float:
        return statistics.median(self.cpu_ms) if self.cpu_ms else 0.0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "iterations": len(self.samples_ms),
            "min_ms": round(self.min_ms, 3),
            "median_ms": round(self.median_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "p90_ms": round(self.p90_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "stdev_ms": round(self.stdev_ms, 3),
            "cpu_median_ms": round(self.cpu_median_ms, 3),
        }


@dataclass
class CaseResult:
    name: str
    group: str
    description: str
    measurements: list[Measurement]
    error: str | None = None
    profile_text: str | None = None
    profile_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "error": self.error,
            "measurements": [measurement.to_dict() for measurement in self.measurements],
            "profile_path": self.profile_path,
        }


def _time_once(fn: Callable[[], None]) -> tuple[float, float]:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    fn()
    cpu_elapsed = time.process_time() - cpu_start
    wall_elapsed = time.perf_counter() - wall_start
    return wall_elapsed * 1000, cpu_elapsed * 1000


class Runner:
    """Runs cases against a fixture and collects measurements."""

    def __init__(
        self,
        fixture: Fixture,
        *,
        iterations: int | None = None,
        warmup: int | None = None,
        profile_iterations: int = 3,
        profile_dir: Path | None = None,
        profile_enabled: bool = False,
    ) -> None:
        self.fixture = fixture
        self.iterations = iterations
        self.warmup = warmup
        self.profile_iterations = profile_iterations
        self.profile_dir = profile_dir
        self.profile_enabled = profile_enabled

    def run(self, case: BenchmarkCase) -> CaseResult:
        if isinstance(case, StepCase):
            return self._run_step_case(case)
        return self._run_case(case)

    def _run_case(self, case: Case) -> CaseResult:
        iterations = self.iterations if self.iterations is not None else case.iterations
        warmup = self.warmup if self.warmup is not None else case.warmup
        measurement = Measurement(label=case.name)
        try:
            self._warmup(case.fn, case.setup, warmup)
            for _ in range(iterations):
                self._setup(case.setup)
                wall_ms, cpu_ms = _time_once(lambda: case.fn(self.fixture))
                measurement.samples_ms.append(wall_ms)
                measurement.cpu_ms.append(cpu_ms)
        except Exception as error:
            return CaseResult(
                case.name,
                case.group,
                case.description,
                [measurement],
                error=f"{type(error).__name__}: {error}",
            )
        profile_text, profile_path = self._maybe_profile_case(case)
        return CaseResult(
            case.name,
            case.group,
            case.description,
            [measurement],
            profile_text=profile_text,
            profile_path=profile_path,
        )

    def _run_step_case(self, case: StepCase) -> CaseResult:
        iterations = self.iterations if self.iterations is not None else case.iterations
        warmup = self.warmup if self.warmup is not None else case.warmup
        measurements = {step.label: Measurement(label=step.label) for step in case.steps}
        try:
            for _ in range(warmup):
                self._setup(case.setup)
                for step in case.steps:
                    step.fn(self.fixture)
            for _ in range(iterations):
                self._setup(case.setup)
                for step in case.steps:
                    wall_ms, cpu_ms = _time_once(lambda step=step: step.fn(self.fixture))
                    measurements[step.label].samples_ms.append(wall_ms)
                    measurements[step.label].cpu_ms.append(cpu_ms)
        except Exception as error:
            return CaseResult(
                case.name,
                case.group,
                case.description,
                list(measurements.values()),
                error=f"{type(error).__name__}: {error}",
            )
        profile_text, profile_path = self._maybe_profile_step_case(case)
        return CaseResult(
            case.name,
            case.group,
            case.description,
            list(measurements.values()),
            profile_text=profile_text,
            profile_path=profile_path,
        )

    def _warmup(
        self, fn: Callable[[Fixture], None], setup: Callable[[Fixture], None] | None, count: int
    ) -> None:
        for _ in range(count):
            self._setup(setup)
            fn(self.fixture)

    def _setup(self, setup: Callable[[Fixture], None] | None) -> None:
        if setup is not None:
            setup(self.fixture)

    def _maybe_profile_case(self, case: Case) -> tuple[str | None, str | None]:
        if not self.profile_enabled or not case.profile or self.profile_dir is None:
            return None, None
        self._setup(case.setup)
        profiler = cProfile.Profile()
        for _ in range(self.profile_iterations):
            profiler.enable()
            case.fn(self.fixture)
            profiler.disable()
            self._setup(case.setup)
        return self._save_profile(case.name, profiler)

    def _maybe_profile_step_case(self, case: StepCase) -> tuple[str | None, str | None]:
        if not self.profile_enabled or not case.profile or self.profile_dir is None:
            return None, None
        self._setup(case.setup)
        profiler = cProfile.Profile()
        for _ in range(self.profile_iterations):
            profiler.enable()
            for step in case.steps:
                step.fn(self.fixture)
            profiler.disable()
            self._setup(case.setup)
        return self._save_profile(case.name, profiler)

    def _save_profile(self, name: str, profiler: cProfile.Profile) -> tuple[str, str]:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        slug = name.replace("/", "-").replace(" ", "_")
        prof_path = self.profile_dir / f"{slug}.prof"
        text_path = self.profile_dir / f"{slug}.txt"
        profiler.dump_stats(str(prof_path))
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative").print_stats(40)
        stats.sort_stats("tottime").print_stats(40)
        text = stream.getvalue()
        text_path.write_text(text)
        return text, str(text_path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def results_payload(results: list[CaseResult], meta: dict) -> dict:
    return {
        "meta": meta,
        "results": [result.to_dict() for result in results],
    }


def to_baseline(results: list[CaseResult]) -> dict:
    """Compact median-per-case form for `--compare`."""
    baseline = {}
    for result in results:
        for measurement in result.measurements:
            baseline[f"{result.name}:{measurement.label}"] = round(measurement.median_ms, 3)
    return baseline


def compare_to_baseline(results: list[CaseResult], baseline: dict) -> list[dict]:
    rows = []
    for result in results:
        for measurement in result.measurements:
            key = f"{result.name}:{measurement.label}"
            if key not in baseline:
                continue
            before = baseline[key]
            after = measurement.median_ms
            change = ((after - before) / before * 100) if before else 0.0
            rows.append(
                {
                    "case": result.name,
                    "step": measurement.label,
                    "baseline_ms": before,
                    "current_ms": round(after, 3),
                    "change_percent": round(change, 1),
                }
            )
    return rows


def load_baseline(path: Path) -> dict:
    return json.loads(path.read_text())


__all__ = [
    "BenchmarkCase",
    "Case",
    "CaseResult",
    "Measurement",
    "Runner",
    "Step",
    "StepCase",
    "compare_to_baseline",
    "load_baseline",
    "results_payload",
    "to_baseline",
    "write_json",
]
