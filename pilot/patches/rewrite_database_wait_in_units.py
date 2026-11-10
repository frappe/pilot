from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Rewrite the systemd units of production benches, so the web unit's database wait gets a
    HOST:PORT address and not the unix: one an older unit passes. Running processes are not restarted:
    the argument is read when the unit starts."""
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.processes.systemd import SystemdProcessManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        manager = SystemdProcessManager(bench)
        production = bench.config.production
        if production.enabled and production.process_manager == "systemd" and manager.is_configured():
            manager.write_config()
            manager.reload_manager_config()
            print(f"Bench '{bench.config.name}' waits for its database on host and port.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
