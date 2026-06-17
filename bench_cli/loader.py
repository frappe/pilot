from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench

# Bench name selected via -b / --bench; set by cli.main() before dispatch.
_active_bench: Optional[str] = None


def set_active_bench(name: Optional[str]) -> None:
    global _active_bench
    _active_bench = name


def cli_root() -> Path:
    import bench_cli as _pkg

    return Path(_pkg.__file__).parent.parent


def find_bench_root() -> Path:
    """
    Locate the directory containing bench.toml for the active bench.

    Resolution order:
    1. -b / --bench <name> flag → benches/<name>/
    2. Exactly one bench in benches/ → use it automatically.
    3. Walk up from cwd (fallback for edge cases).
    """
    benches_dir = cli_root() / "benches"

    if _active_bench:
        bench_dir = benches_dir / _active_bench
        if not (bench_dir / "bench.toml").exists():
            candidates = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()] if benches_dir.is_dir() else []
            hint = f"  Available: {', '.join(candidates)}" if candidates else "  No benches found. Run: bench new <name>"
            raise BenchError(f"Bench '{_active_bench}' not found.\n{hint}")
        return bench_dir

    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: bench -b <name> <command>")

    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory

    raise BenchError("No bench found. Create one with: bench new <name>")


def load_bench() -> "Bench":
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench

    bench_root = find_bench_root()
    config = BenchConfig.from_file(bench_root / "bench.toml")
    return Bench(config, bench_root)
