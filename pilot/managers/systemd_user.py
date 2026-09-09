from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from pilot.utils import run_command


def systemctl(*args: str) -> list[str]:
    return ["systemctl", "--user", *args]


def systemctl_env() -> dict:
    env = dict(os.environ)
    runtime_dir = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # systemd-run silently ignores resource limits when it cannot reach the user
    # bus: the scope starts, reports itself running, and stays uncapped.
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def user_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def has_user_memory_control() -> bool:
    """Whether transient scopes can cap memory here. Needs systemd-run and a
    memory controller delegated to this user's slice."""
    if not shutil.which("systemd-run"):
        return False
    controllers = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/cgroup.controllers")
    try:
        return "memory" in controllers.read_text().split()
    except OSError:
        return False


def memory_capped(argv: list[str], memory_max_mb: int) -> list[str]:
    """Run argv in a transient scope the kernel kills past memory_max_mb. Returns
    argv unchanged where scopes cannot cap, so callers stay one code path."""
    if not has_user_memory_control():
        logging.warning(
            "Memory control is unavailable here, so this build runs uncapped and "
            "can exhaust the machine. Needs systemd-run and a delegated memory "
            "controller on the user slice."
        )
        return argv
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        # Reap the scope when it dies, so a killed build leaves nothing behind.
        "--collect",
        "-p",
        f"MemoryMax={memory_max_mb}M",
        # Without this the cap is absorbed by swap and the build grinds instead.
        "-p",
        "MemorySwapMax=0",
        *argv,
    ]


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
