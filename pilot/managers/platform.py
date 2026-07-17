import os
import platform
import shutil
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Iterator

# sbin/bin dirs a minimal PATH often omits (e.g. /usr/sbin for mariadbd/nginx).
_EXTRA_BIN_DIRS = ("/usr/local/sbin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/bin", "/bin")
NONINTERACTIVE_PRIVILEGES_ENV = "PILOT_NONINTERACTIVE_PRIVILEGES"
_NONINTERACTIVE_PRIVILEGES: ContextVar[bool] = ContextVar(
    "noninteractive_privileges",
    default=False,
)


def which(name: str) -> str | None:
    """``shutil.which`` that also searches the standard sbin/bin dirs."""
    path = os.environ.get("PATH", os.defpath)
    return shutil.which(name, path=os.pathsep.join([path, *_EXTRA_BIN_DIRS]))


class Platform(Enum):
    LINUX = "linux"
    MACOS = "macos"


def detect() -> Platform:
    if platform.system() == "Darwin":
        return Platform.MACOS
    return Platform.LINUX


def is_macos() -> bool:
    return detect() == Platform.MACOS


def is_linux() -> bool:
    return detect() == Platform.LINUX


class Distro(Enum):
    DEBIAN = "debian"
    UBUNTU = "ubuntu"
    FEDORA = "fedora"
    ARCH = "arch"
    UNKNOWN = "unknown"


_OS_RELEASE = Path("/etc/os-release")


def _read_os_release() -> dict[str, str]:
    if not _OS_RELEASE.exists():
        return {}
    fields = {}
    for line in _OS_RELEASE.read_text().splitlines():
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def detect_distro() -> Distro:
    """Identify the Linux distribution from /etc/os-release.

    Falls back to ID_LIKE so derivatives map onto their parent (e.g. Linux
    Mint -> UBUNTU, EndeavourOS -> ARCH)."""
    if not is_linux():
        return Distro.UNKNOWN
    fields = _read_os_release()
    known = {distro.value for distro in Distro}
    if fields.get("ID") in known:
        return Distro(fields["ID"])
    for token in fields.get("ID_LIKE", "").split():
        if token in known:
            return Distro(token)
    return Distro.UNKNOWN


def os_version() -> str:
    """Best-effort human-readable OS name and version.

    e.g. 'Ubuntu 22.04.4 LTS', 'Debian GNU/Linux 12 (bookworm)', or 'macOS 14.5'.
    Falls back to the bare platform/release string when nothing more specific
    is available."""
    if is_macos():
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else "macOS"
    pretty_name = _read_os_release().get("PRETTY_NAME")
    if pretty_name:
        return pretty_name
    return f"{platform.system()} {platform.release()}".strip()


def kernel_version() -> str:
    """Best-effort kernel version (e.g. '6.8.0-40-generic' or Darwin's '23.5.0')."""
    return platform.release()


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _privileged(command: list[str]) -> list[str]:
    """Prefix a command with sudo unless we are already root."""
    if is_root():
        return command
    noninteractive = (
        _NONINTERACTIVE_PRIVILEGES.get()
        or os.environ.get(NONINTERACTIVE_PRIVILEGES_ENV) == "1"
    )
    sudo = ["sudo", "-n"] if noninteractive else ["sudo"]
    return [*sudo, *command]


@contextmanager
def noninteractive_privileges() -> Iterator[None]:
    """Make sudo fail instead of prompting in the current execution context."""
    token = _NONINTERACTIVE_PRIVILEGES.set(True)
    try:
        yield
    finally:
        _NONINTERACTIVE_PRIVILEGES.reset(token)


def has_passwordless_sudo() -> bool:
    """True if no password prompt blocks privileged commands — already root, or
    sudo runs non-interactively."""
    if is_root():
        return True
    if which("sudo") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0


def service_command(action: str, name: str) -> list[str]:
    """Return the privileged argv to run a systemd action (start/stop/restart/reload)."""
    return _privileged(["systemctl", action, name])


def service_enable_command(name: str) -> list[str]:
    """Return the privileged argv to enable a service at boot."""
    return _privileged(["systemctl", "enable", name])


def service_disable_command(name: str) -> list[str]:
    """Return the privileged argv to disable a service at boot."""
    return _privileged(["systemctl", "disable", name])


def service_running(name: str) -> bool:
    """Return True if the named system service is currently running."""
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", name], capture_output=True, timeout=5
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def native_process_manager() -> str:
    """The init system used to manage production benches on this host.

    systemd is the recommended native manager; the supervisor manager is the
    cross-platform alternative and is never the platform default. UI and CLI
    deploy paths use this to offer the right default instead of assuming systemd.
    """
    return "systemd"


def default_nginx_config_dir() -> Path:
    """Directory nginx includes server blocks from (``/etc/nginx/conf.d``)."""
    return Path("/etc/nginx/conf.d")


_HOSTS_PATH = Path("/etc/hosts")


def add_hosts_entry(hostname: str, hosts_path: Path = _HOSTS_PATH) -> None:
    """Add a 127.0.0.1 entry for `hostname` to /etc/hosts, unless already
    present. Always non-interactive (sudo -n): a best-effort dev convenience,
    it must never block on a password prompt."""
    from pilot.utils import hosts_line_contains

    entry = f"127.0.0.1 {hostname}"
    for line in hosts_path.read_text().splitlines():
        if hosts_line_contains(line, hostname):
            return
    try:
        subprocess.run(
            ["sudo", "-n", "tee", "-a", str(hosts_path)],
            input=f"{entry}\n".encode(),
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        print(
            f"Warning: could not add '{entry}' to {hosts_path}: {e}.\n"
            f"  Add it manually to reach the site by name.",
            file=sys.stderr,
        )


def remove_hosts_entry(hostname: str, hosts_path: Path = _HOSTS_PATH) -> None:
    """Remove any 127.0.0.1 entry for `hostname` from /etc/hosts, if present."""
    from pilot.utils import hosts_line_contains

    try:
        lines = hosts_path.read_text().splitlines()
    except OSError:
        return
    kept = [line for line in lines if not hosts_line_contains(line, hostname)]
    if len(kept) == len(lines):
        return
    subprocess.run(
        ["sudo", "-n", "tee", str(hosts_path)],
        input=("\n".join(kept) + "\n").encode(),
        capture_output=True,
        check=False,
    )


def unmount_legacy_bind_mount(target: Path, fstab_path: Path = Path("/etc/fstab")) -> None:
    """Unmount `target` and drop its fstab entry if present.

    Benches created before ZFS/volume support was removed may still have
    their directory (or a dedicated MariaDB datadir) bind-mounted from an
    old dataset, with a matching fstab line so it survived reboots. This
    doesn't depend on ZFS or any volume-management code being present —
    it only looks at whether `target` is currently a mountpoint — so it's
    a no-op, and safe to call unconditionally, for any bench that was
    never volume-backed.
    """
    try:
        is_mounted = target.is_mount()
    except OSError:
        is_mounted = False
    if is_mounted:
        print(f"Unmounting legacy bind mount at {target}...", flush=True)
        try:
            subprocess.run(_privileged(["umount", "-l", str(target)]), check=False)
        except Exception as exc:
            print(f"  (unmount {target} skipped: {exc})")

    try:
        lines = fstab_path.read_text().splitlines()
    except OSError:
        return
    kept = [
        line for line in lines
        if not (
            len(line.split()) >= 2
            and not line.lstrip().startswith("#")
            and line.split()[1] == str(target)
        )
    ]
    if len(kept) == len(lines):
        return
    content = "\n".join(kept) + "\n"
    try:
        subprocess.run(
            _privileged(["tee", str(fstab_path)]),
            input=content.encode(),
            capture_output=True,
            check=True,
        )
    except Exception as exc:
        print(f"  (fstab cleanup for {target} skipped: {exc})")
