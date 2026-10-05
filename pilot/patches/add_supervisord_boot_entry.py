from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Add the @reboot crontab entry that starts a production supervisor bench's
    supervisord, which nothing restarted after a reboot before."""
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.processes.supervisor import SupervisorProcessManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        production = bench.config.production
        manager = SupervisorProcessManager(bench)
        if production.enabled and production.process_manager == "supervisor" and manager.is_configured():
            manager.install_config()
            print(f"Bench '{bench.config.name}' now starts supervisord after a reboot.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
