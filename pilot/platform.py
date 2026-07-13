import os
import platform
import shutil
import subprocess
from enum import Enum
from pathlib import Path

# sbin/bin dirs a minimal PATH often omits (e.g. /usr/sbin for mariadbd/nginx).
_EXTRA_BIN_DIRS = ("/usr/local/sbin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/bin", "/bin")


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
    ALPINE = "alpine"
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


def is_alpine() -> bool:
    """Return True on Alpine Linux (apk package manager, OpenRC, musl libc)."""
    if not is_linux():
        return False
    return Path("/etc/alpine-release").exists() or detect_distro() == Distro.ALPINE


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
    """Prefix a command with sudo unless we are already root.

    Alpine images commonly run as root, so dropping the prefix when euid is 0
    keeps installs and service calls working whether or not sudo is present.
    """
    if is_root():
        return command
    return ["sudo", *command]


def has_passwordless_sudo() -> bool:
    """True if no password prompt blocks privileged commands — already root, or
    sudo runs non-interactively."""
    if is_root():
        return True
    if which("sudo") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def service_command(action: str, name: str) -> list[str]:
    """Return the privileged argv to run an init action (start/stop/restart/reload).

    Alpine uses OpenRC (``rc-service``); other Linux servers use systemd
    (``systemctl``).
    """
    if is_alpine():
        return _privileged(["rc-service", name, action])
    return _privileged(["systemctl", action, name])


def service_enable_command(name: str) -> list[str]:
    """Return the privileged argv to enable a service at boot."""
    if is_alpine():
        return _privileged(["rc-update", "add", name, "default"])
    return _privileged(["systemctl", "enable", name])


def service_disable_command(name: str) -> list[str]:
    """Return the privileged argv to disable a service at boot."""
    if is_alpine():
        return _privileged(["rc-update", "del", name, "default"])
    return _privileged(["systemctl", "disable", name])


def service_running(name: str) -> bool:
    """Return True if the named system service is currently running."""
    if is_alpine():
        # rc-service lives in /sbin (off a non-root login PATH) and reads
        # root-owned run state, so query it with privilege — otherwise it's
        # "command not found" for the bench user and every service looks stopped.
        argv = _privileged(["rc-service", name, "status"])
    else:
        argv = ["systemctl", "is-active", "--quiet", name]
    try:
        return subprocess.run(argv, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def native_process_manager() -> str:
    """The init system used to manage production benches on this host.

    OpenRC on Alpine, systemd everywhere else. This is the recommended (and, on
    Alpine, the only available) native manager; the supervisor manager is the
    cross-platform alternative and is never the platform default. UI and CLI
    deploy paths use this to offer the right default instead of assuming systemd.
    """
    return "openrc" if is_alpine() else "systemd"


def default_nginx_config_dir() -> Path:
    """Directory nginx includes server blocks from (distro-specific).

    Alpine's nginx includes ``/etc/nginx/http.d/*.conf``; Debian/Ubuntu use
    ``/etc/nginx/conf.d/``.
    """
    if is_alpine():
        return Path("/etc/nginx/http.d")
    return Path("/etc/nginx/conf.d")


