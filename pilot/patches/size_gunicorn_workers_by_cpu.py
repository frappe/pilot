from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem
_OLD_DEFAULT_WORKERS = 2


def run(benches_root: Path) -> None:
    """Drop the old default of 2 gunicorn workers, so the bench runs one per CPU."""
    from pilot.config import BenchConfig
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        with BenchConfig.open(bench_dir, mode="raw") as data:
            gunicorn = data.get("gunicorn", {})
            is_changed = gunicorn.get("workers") == _OLD_DEFAULT_WORKERS
            if is_changed:
                del gunicorn["workers"]
        if is_changed:
            reload_web(Bench(bench_dir))
        mark_applied(bench_dir, PATCH_NAME)


def reload_web(bench) -> None:
    """Gunicorn reads its worker count at start. Lite mode does not run gunicorn."""
    from pilot.managers.gunicorn import GunicornManager
    from pilot.managers.processes.local import ProcessManager

    GunicornManager(bench).generate_config()
    if not bench.config.production.enabled or bench.is_lite_mode:
        return
    manager = ProcessManager.detect_running(bench)
    if manager.is_running():
        manager.reload_workers(web_only=True)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
