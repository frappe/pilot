from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.database.mariadb import MariaDBManager
    from pilot.managers.platform import is_linux

    if not is_linux():
        return
    for bench_dir in sorted(benches_root.glob("*")):
        if not (bench_dir / "bench.toml").is_file() or is_applied(bench_dir, PATCH_NAME):
            continue
        config = BenchConfig.read(bench_dir)
        if config.db_type == "mariadb" and not config.mariadb.existing:
            manager = MariaDBManager(config.mariadb)
            if manager.is_provisioned():
                manager.memory.install_existing()
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
