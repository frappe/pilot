from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Give existing sites the default scheduled backups that new sites get. A site with a
    retention record has a custom schedule or the default already, and keeps it."""
    from pilot.config.site_backup import read_retention
    from pilot.core.bench import Bench
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        bench = Bench(bench_dir)
        for site in bench.sites():
            if read_retention(site.path / "site_config.json") is not None:
                continue
            site.backups.enable_default_schedule()
            print(f"Site '{site.config.name}' now has scheduled backups.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
