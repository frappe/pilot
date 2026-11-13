from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Install the unit that sizes Pilot's MariaDB before each start. One MariaDB serves
    every bench, so the first bench installs it."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.database import MariaDBManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        config = BenchConfig.read(bench_dir)
        manager = MariaDBManager(config.mariadb)
        if _has_own_mariadb(config, manager):
            manager.install_sizing_unit()
            print("Installed the MariaDB sizing unit.")
        mark_applied(bench_dir, PATCH_NAME)


def _has_own_mariadb(config, manager) -> bool:
    """Whether the bench owns a managed MariaDB on Linux."""
    from pilot.managers.platform import HostPlatform

    return (
        config.db_type == "mariadb"
        and not HostPlatform().is_macos
        and not config.mariadb.existing
        and manager.unit_path.exists()
    )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
