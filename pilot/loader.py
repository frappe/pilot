from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.context import CliContext
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def cli_root() -> Path:
    import pilot as _pkg

    return Path(_pkg.__file__).parent.parent


def find_bench_root(context: CliContext, require_explicit: bool = False) -> Path:
    """Resolve the bench dir: -b/--bench, then the enclosing dir, then the sole bench."""
    benches_dir = context.installation_root / "benches"

    if context.bench_name:
        bench_dir = benches_dir / context.bench_name
        if not (bench_dir / "bench.toml").exists():
            raise BenchError(f"Bench '{context.bench_name}' not found.\n{_available_hint(benches_dir)}")
        return bench_dir

    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory

    if require_explicit:
        raise BenchError(
            "This command needs an explicit bench — run it from inside the bench "
            "directory, or pass -b <name>.\n" + _available_hint(benches_dir, sort=True)
        )

    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: bench -b <name> <command>")

    raise BenchError("No bench found. Create one with: bench new <name>")


def _available_hint(benches_dir: Path, sort: bool = False) -> str:
    if not benches_dir.is_dir():
        return "  No benches found. Run: bench new <name>"
    names = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
    if not names:
        return "  No benches found. Run: bench new <name>"
    return f"  Available: {', '.join(sorted(names) if sort else names)}"


def load_bench(context: CliContext, require_explicit: bool = False) -> "Bench":
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    bench_root = find_bench_root(context, require_explicit=require_explicit)
    config = BenchTomlStore.for_bench(bench_root).read()
    return Bench(config, bench_root)
