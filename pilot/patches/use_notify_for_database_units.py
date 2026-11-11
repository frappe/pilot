from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Make Pilot's database units report ready to systemd, and drop the web unit's own database wait.
    Only files change and systemd reloads them. Nothing is restarted."""
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.database import MariaDBManager, PostgresManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        if bench.config.db_type == "postgres":
            PostgresManager(bench.config.postgres).use_notify_type()
        elif bench.config.db_type == "mariadb":
            MariaDBManager(bench.config.mariadb).use_notify_type()
        drop_web_database_wait(bench)
        mark_applied(bench_dir, PATCH_NAME)


def drop_web_database_wait(bench: Bench) -> None:
    """Rewrite the units of a systemd bench. Its web unit ran `wait-for-database` before it started,
    and that command is removed."""
    from pilot.managers.processes.systemd import SystemdProcessManager

    production = bench.config.production
    if not production.enabled or production.process_manager != "systemd":
        return
    manager = SystemdProcessManager(bench)
    if not manager.is_configured():
        return
    manager.write_config()
    manager.reload_manager_config()
    print(f"Bench '{bench.config.name}' starts its web process without its own database wait.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
