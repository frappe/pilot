from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Move each site's backup runs from private/backups, which Frappe prunes, to the
    site's backups directory. A run already moved is skipped."""
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        for site_dir in sorted((bench_dir / "sites").glob("*/private/backups")):
            move_backup_runs(site_dir, site_dir.parent.parent / "backups")
        mark_applied(bench_dir, PATCH_NAME)


def move_backup_runs(source: Path, target: Path) -> None:
    from pilot.core.site.backups import parse_backup_timestamp

    for path in sorted(source.iterdir()):
        if not path.is_file() or not parse_backup_timestamp(path.name):
            continue
        target.mkdir(exist_ok=True)
        if not (target / path.name).exists():
            path.rename(target / path.name)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
