from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Raise the shared MariaDB unit's MemoryHigh to sit just under MemoryMax, live and
    without a restart. One MariaDB serves every bench, so the first bench applies it."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.database import MariaDBManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        config = BenchConfig.read(bench_dir)
        if config.db_type == "mariadb" and MariaDBManager(config.mariadb).raise_memory_high():
            print("Raised MariaDB MemoryHigh to sit just under MemoryMax.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
