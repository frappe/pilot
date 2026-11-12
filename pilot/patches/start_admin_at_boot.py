from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Start the socket-activated Admin at boot, so its worker runs tasks queued before a reboot."""
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
        if bench.config.production.enabled and manager.is_configured():
            manager.write_config()
            manager.reload_manager_config()
            manager.enable_admin_at_boot()
            print(f"Bench '{bench.config.name}' now starts its Admin at boot.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
