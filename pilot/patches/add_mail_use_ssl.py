from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Add use_ssl to a mailbox stored as SSL with only use_tls 0, so the framework
    connects over SSL too."""
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        if (bench_dir / "sites" / "common_site_config.json").exists():
            add_use_ssl(bench_dir / "sites")
        mark_applied(bench_dir, PATCH_NAME)


def add_use_ssl(sites_path: Path) -> None:
    """Add use_ssl to the common site config."""
    from pilot.config.common_site_config import update_common_site_config

    with update_common_site_config(sites_path) as config:
        if config.get("mail_server") and config.get("use_tls") == 0 and "use_ssl" not in config:
            config["use_ssl"] = 1


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
