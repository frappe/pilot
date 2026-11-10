from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Make Pilot's database units report ready to systemd, so units ordered after them wait for the
    database. The files change and systemd reloads them; a running server is not restarted."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.database import MariaDBManager, PostgresManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        config = BenchConfig.read(bench_dir)
        if config.db_type == "postgres":
            PostgresManager(config.postgres).use_notify_type()
        elif config.db_type == "mariadb":
            MariaDBManager(config.mariadb).use_notify_type()
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
