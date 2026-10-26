from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem

MONITOR_SERVICE = "pilot-monitor.service"
LEGACY_TIMERS = ("pilot-monitor.timer", "pilot-uptime.timer")
LEGACY_UPTIME_SERVICE = "pilot-uptime.service"


def run(benches_root: Path) -> None:
    """Replace the pilot-monitor and pilot-uptime timers with the long-running pilot-monitor
    service. The monitor is host-wide, so the first bench that needs it replaces it."""
    from pilot.internal.patch_state import is_applied, mark_applied

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        if _is_replacement_needed():
            _replace_timers()
            print("Replaced the monitor and uptime timers with the pilot-monitor service.")
        mark_applied(bench_dir, PATCH_NAME)


def _is_replacement_needed() -> bool:
    """A host still on the timers, or one where an earlier replacement stopped before the service
    was enabled and running."""
    from pilot.managers.systemd_user import user_service_installed, user_timer_installed

    if any(user_timer_installed(timer) for timer in LEGACY_TIMERS):
        return True
    return user_service_installed(MONITOR_SERVICE) and not _is_running_at_boot()


def _replace_timers() -> None:
    import subprocess

    from pilot.core.server.monitoring_config import MonitorConfigurator
    from pilot.exceptions import BenchError
    from pilot.managers.systemd_user import systemctl, systemctl_env, user_unit_dir
    from pilot.utils import cli_root

    env = systemctl_env()
    subprocess.run(systemctl("disable", "--now", *LEGACY_TIMERS), capture_output=True, env=env, check=False)
    # A oneshot run in progress must finish before its unit becomes the long-running service.
    subprocess.run(
        systemctl("stop", MONITOR_SERVICE, LEGACY_UPTIME_SERVICE), capture_output=True, env=env, check=False
    )

    # The pilot-monitor.service link stays, so a failed install below is retried on the next run.
    unit_files = [
        cli_root() / "system" / "monitor" / "pilot-monitor.timer",
        cli_root() / "system" / "uptime" / "pilot-uptime.timer",
        cli_root() / "system" / "uptime" / LEGACY_UPTIME_SERVICE,
        *(user_unit_dir() / name for name in (*LEGACY_TIMERS, LEGACY_UPTIME_SERVICE)),
    ]
    for path in unit_files:
        path.unlink(missing_ok=True)

    monitor = MonitorConfigurator()
    monitor.install()
    # A run that failed after writing the unit leaves install() a no-op, and the service stopped
    # and not enabled.
    subprocess.run(systemctl("enable", MONITOR_SERVICE), capture_output=True, env=env, check=False)
    monitor.restart()
    # systemd keeps the removed uptime units in memory as failed until reset.
    subprocess.run(
        systemctl("reset-failed", *LEGACY_TIMERS, LEGACY_UPTIME_SERVICE),
        capture_output=True,
        env=env,
        check=False,
    )
    if not _is_running_at_boot():
        raise BenchError(f"{MONITOR_SERVICE} is not enabled and running. See system/logs/monitor.error.log.")


def _is_running_at_boot() -> bool:
    """True when the monitor service runs now and starts again after a reboot."""
    import subprocess

    from pilot.managers.systemd_user import systemctl, systemctl_env

    env = systemctl_env()
    return all(
        subprocess.run(systemctl(check, "--quiet", MONITOR_SERVICE), env=env, check=False).returncode == 0
        for check in ("is-enabled", "is-active")
    )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
