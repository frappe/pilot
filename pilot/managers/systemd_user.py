from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pilot.internal.command import run_command


def systemctl(*args: str) -> list[str]:
    """The systemctl --user command."""
    return ["systemctl", "--user", *args]


def systemctl_env() -> dict:
    """The environment systemctl --user needs."""
    env = dict(os.environ)
    runtime_dir = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # Without the bus address systemd-run starts an uncapped scope and reports success.
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def can_cap_memory() -> bool:
    """Whether a build can run in a memory-capped scope. A trial scope catches a host without
    a user bus."""
    controllers = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/cgroup.controllers")
    try:
        if not shutil.which("systemd-run") or "memory" not in controllers.read_text().split():
            return False
        trial = subprocess.run(
            memory_capped(["true"], 64, 0), env=systemctl_env(), capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return trial.returncode == 0


# systemd's exit status when it cannot set up a unit's namespaces.
_EXIT_NAMESPACE = 226


def get_sandbox_error(properties: list[str]) -> str:
    """Why a trial unit with these properties fails to start here, or "" when it runs."""
    if not shutil.which("systemd-run"):
        return "systemd-run is not installed."
    argv = ["systemd-run", "--user", "--wait", "--quiet", "--collect"]
    for directive in properties:
        argv += ["-p", directive]
    try:
        trial = subprocess.run(
            [*argv, "true"], env=systemctl_env(), capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if trial.returncode == 0:
        return ""
    if trial.returncode == _EXIT_NAMESPACE:
        return (
            "systemd could not create the sandbox's user namespace (exit 226). "
            "The host blocks unprivileged user namespaces; on Ubuntu 24.04 that is "
            "kernel.apparmor_restrict_unprivileged_userns = 1."
        )
    return trial.stderr.strip() or f"systemd-run exited with code {trial.returncode}."


def memory_capped(argv: list[str], memory_max_mb: int, swap_max_mb: int) -> list[str]:
    """Run argv in a transient scope capped at memory_max_mb of RAM and swap_max_mb of
    swap. Only where `can_cap_memory` holds. Swap lets small hosts finish large builds."""
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "-p",
        f"MemoryMax={memory_max_mb}M",
        "-p",
        f"MemorySwapMax={swap_max_mb}M",
        *argv,
    ]


def user_unit_dir() -> Path:
    """The user systemd unit directory."""
    return Path.home() / ".config" / "systemd" / "user"


class SystemdUserMixin:
    """Shared `systemctl --user` plumbing for anything that installs or
    controls per-user systemd units (databases, bench process units, the
    shared monitor/uptime timers)."""

    def _systemctl(self, *args: str) -> list[str]:
        """The systemctl --user command."""
        return systemctl(*args)

    def _systemctl_env(self) -> dict:
        """The environment systemctl --user needs."""
        return systemctl_env()

    @property
    def user_unit_dir(self) -> Path:
        """The user systemd unit directory."""
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
    ~/.config/systemd/user, then reload and enable the timer."""
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
