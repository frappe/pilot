from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Bench name selected via -b / --bench; set by cli.main() before dispatch.
_active_bench: Optional[str] = None


def set_active_bench(name: Optional[str]) -> None:
    global _active_bench
    _active_bench = name


def cli_root() -> Path:
    import pilot as _pkg

    return Path(_pkg.__file__).parent.parent


def find_bench_root(require_explicit: bool = False) -> Path:
    """
    Locate the directory containing bench.toml for the active bench.

    Resolution order:
    1. -b / --bench <name> flag → benches/<name>/
    2. Walk up from cwd → the bench you're inside wins (even when many exist).
    3. Exactly one bench in benches/ → use it automatically.
    4. Multiple benches and no other signal → ask for -b.

    With ``require_explicit``, only (1) and (2) apply — auto-pick (3) is off.
    """
    benches_dir = cli_root() / "benches"

    if _active_bench:
        bench_dir = benches_dir / _active_bench
        if not (bench_dir / "bench.toml").exists():
            candidates = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()] if benches_dir.is_dir() else []
            hint = f"  Available: {', '.join(candidates)}" if candidates else "  No benches found. Run: bench new <name>"
            raise BenchError(f"Bench '{_active_bench}' not found.\n{hint}")
        return bench_dir

    # Inside a bench directory? Use it — this takes precedence over the
    # multiple-benches ambiguity below, so `cd benches/x && bench <cmd>` works.
    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory

    if require_explicit:
        candidates = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()] if benches_dir.is_dir() else []
        hint = f"  Available: {', '.join(sorted(candidates))}" if candidates else "  No benches found. Run: bench new <name>"
        raise BenchError(
            "This command needs an explicit bench — run it from inside the bench "
            "directory, or pass -b <name>.\n" + hint
        )

    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: bench -b <name> <command>")

    raise BenchError("No bench found. Create one with: bench new <name>")


def load_bench(require_explicit: bool = False) -> "Bench":
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    bench_root = find_bench_root(require_explicit=require_explicit)
    config = BenchTomlStore.for_bench(bench_root).read()
    return Bench(config, bench_root)
