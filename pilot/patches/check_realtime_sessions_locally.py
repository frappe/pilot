from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Point realtime's session checks at local web, so they skip nginx and the site's TLS certificate."""
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        if (bench.sites_path / "common_site_config.json").exists():
            check_sessions_locally(bench)
        mark_applied(bench_dir, PATCH_NAME)


def check_sessions_locally(bench) -> None:
    from pilot.config.common_site_config import update_common_site_config
    from pilot.managers.processes.definitions import ProcessDefinitionBuilder
    from pilot.managers.processes.local import ProcessManager

    with update_common_site_config(bench.sites_path) as config:
        config["webserver_host"] = "127.0.0.1"

    # The socketio server reads its config once, at start. Lite mode has none.
    if not bench.config.production.enabled or bench.is_lite_mode:
        return
    manager = ProcessManager.for_bench(bench)
    if manager.is_running():
        builder = ProcessDefinitionBuilder(bench, manager.python, watch_admin_js=False)
        manager.restart_processes([builder.socketio_definition()])


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
