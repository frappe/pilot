"""BenchConfig and CommonConfig load, validate, and serialize benchmarks."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.config import BenchConfig
from pilot.config.common import CommonConfig

CASES = [
    Case(
        name="config.read",
        group="config",
        description="Read bench.toml + common_config.toml, validate, build dataclasses",
        fn=lambda fixture: BenchConfig.read(fixture.bench_root),
        iterations=30,
        warmup=5,
        profile=True,
    ),
    Case(
        name="config.read_unvalidated",
        group="config",
        description="Read bench.toml without validation (parse-only, used by `pilot ls`)",
        fn=lambda fixture: BenchConfig.read(fixture.bench_root, validate=False),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="config.read_raw",
        group="config",
        description="Parse bench.toml to a plain dict (Toml.loads only)",
        fn=lambda fixture: BenchConfig.read_raw(fixture.bench_root),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="config.common_read",
        group="config",
        description="Read and parse common_config.toml",
        fn=lambda fixture: CommonConfig.read(fixture.benches_root),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="config.dumps",
        group="config",
        description="Serialize a loaded config back to TOML",
        fn=lambda fixture: BenchConfig.read(fixture.bench_root).dumps(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="config.write",
        group="config",
        description="Validate, serialize, and atomically write bench.toml + common_config.toml",
        fn=lambda fixture: BenchConfig.read(fixture.bench_root).write(fixture.bench_root),
        setup=lambda fixture: fixture.reset(),
        iterations=10,
        warmup=2,
        profile=True,
    ),
    Case(
        name="config.default",
        group="config",
        description="Build a fresh wizard-default config",
        fn=lambda fixture: BenchConfig.default("bench", benches_root=fixture.benches_root),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="config.from_flat",
        group="config",
        description="Build a config from the wizard's flat settings",
        fn=lambda fixture: BenchConfig.from_flat(
            "bench", {"admin_enabled": True}, benches_root=fixture.benches_root
        ),
        iterations=20,
        warmup=3,
    ),
]
