from __future__ import annotations

import os
from pathlib import Path

from pilot.utils import run_command


def systemctl(*args: str) -> list[str]:
    return ["systemctl", "--user", *args]


def systemctl_env() -> dict:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def user_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


class SystemdUserMixin:
    """Shared `systemctl --user` plumbing for anything that installs or
    controls per-user systemd units (databases, bench process units, the
    shared monitor/uptime timers)."""

    def _systemctl(self, *args: str) -> list[str]:
        return systemctl(*args)

    def _systemctl_env(self) -> dict:
        return systemctl_env()

    @property
    def user_unit_dir(self) -> Path:
        return user_unit_dir()


def install_user_timer(
    *,
    unit_dir: Path,
    unit_name: str,
    unit_text: str,
    timer_unit_name: str,
    timer_text: str,
) -> None:
    """Write a oneshot service + its timer into unit_dir, symlink both into
    ~/.config/systemd/user, then reload and enable the timer. Shared by
    MonitorConfigurator and UptimeMonitorConfigurator - identical install
    shape, different unit content."""
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / unit_name
    timer_path = unit_dir / timer_unit_name
    service_path.write_text(unit_text)
    timer_path.write_text(timer_text)

    dest_dir = user_unit_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, target in ((unit_name, service_path), (timer_unit_name, timer_path)):
        link = dest_dir / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target.resolve())

    env = systemctl_env()
    run_command(systemctl("daemon-reload"), env=env)
    run_command(systemctl("enable", "--now", timer_unit_name), env=env)


def user_timer_installed(timer_unit_name: str) -> bool:
    """True if the timer's already symlinked into ~/.config/systemd/user -
    mirrors UserOwnedDBManager.is_provisioned()'s existence check."""
    return (user_unit_dir() / timer_unit_name).exists()


def install_user_service(
    *,
    unit_dir: Path,
    unit_name: str,
    unit_text: str,
) -> None:
    """Write a long-running service unit into unit_dir, symlink it into
    ~/.config/systemd/user, then reload and enable --now. Sibling of
    `install_user_timer` for services that run continuously (Fluent Bit),
    not on a timer."""
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / unit_name
    service_path.write_text(unit_text)

    dest_dir = user_unit_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    link = dest_dir / unit_name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(service_path.resolve())

    env = systemctl_env()
    run_command(systemctl("daemon-reload"), env=env)
    run_command(systemctl("enable", "--now", unit_name), env=env)


def user_service_installed(unit_name: str) -> bool:
    """True if the service is already symlinked into ~/.config/systemd/user."""
    return (user_unit_dir() / unit_name).exists()
