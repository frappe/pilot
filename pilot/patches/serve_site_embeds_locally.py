from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Publish embed routes before changing existing sites' browser URLs."""
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.nginx import NginxManager

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        if bench.config.production.enabled:
            NginxManager(bench).apply_config()
            bench.configure_site_embeds()
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
