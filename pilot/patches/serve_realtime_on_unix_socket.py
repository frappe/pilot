from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Move production realtime from its TCP port to a Unix socket that only nginx reaches."""
    from pilot.core.bench import Bench
    from pilot.internal.command import run_command
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.nginx import NginxManager
    from pilot.managers.processes.systemd import SystemdProcessManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        manager = SystemdProcessManager(bench)
        if bench.config.production.enabled and bench.realtime_socket and manager.is_configured():
            manager.write_config()
            manager.reload_manager_config()
            run_command(
                manager._systemctl("try-restart", manager._unit_name("socketio")), env=manager._systemctl_env()
            )
            NginxManager(bench).reload_for_site_change()
            print(f"Bench '{bench.config.name}' now serves realtime on a Unix socket.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
