from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Write `sandbox = false` for each bench that has no setting. New benches run in the sandbox by
    default, and a bench that already runs should not change how it runs on an update."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        with BenchConfig.open(bench_dir, "raw") as data:
            data.setdefault("production", {}).setdefault("sandbox", False)
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
