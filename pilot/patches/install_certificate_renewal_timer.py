from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Install the daily certificate renewal timer on hosts that already issue Let's Encrypt
    certificates. The timer is host-wide, so the first bench that needs it installs it."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.letsencrypt import install_renewal_timer, is_renewal_timer_installed

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        if _is_issuing_certificates(BenchConfig.read(bench_dir)) and not is_renewal_timer_installed():
            install_renewal_timer()
            print("Installed the certificate renewal timer.")
        mark_applied(bench_dir, PATCH_NAME)


def _is_issuing_certificates(config) -> bool:
    from pilot.managers.platform import HostPlatform

    return HostPlatform().is_linux and config.production.enabled and bool(config.letsencrypt.email)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.core.server import Server

    run(Server().benches_dir)
