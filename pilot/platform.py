import os
import platform
import shutil
import subprocess
from abc import ABC, abstractmethod
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


def is_x86_64() -> bool:
    return platform.machine().lower() in ("x86_64", "amd64")


def is_alpine() -> bool:
    """Return True on Alpine Linux (apk package manager, OpenRC, musl libc)."""
    if not is_linux():
        return False
    if Path("/etc/alpine-release").exists():
        return True
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return False
    for line in os_release.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ID" and value.strip().strip('"') == "alpine":
            return True
    return False


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


class SystemPackageManager(ABC):
    # Maps the canonical (Debian/apt) package name used at call sites to the name
    # this package manager understands. Names absent from the map pass through.
    package_aliases: dict[str, str] = {}

    def _resolve(self, *packages: str) -> list[str]:
        return [self.package_aliases.get(package, package) for package in packages]

    @abstractmethod
    def install(self, *packages: str) -> None:
        """Install one or more system packages."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        """Return True if the package is already installed."""

    @abstractmethod
    def update(self) -> None:
        """Update package manager"""


class AptPackageManager(SystemPackageManager):
    def install(self, *packages: str) -> None:
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", *packages],
            env={"DEBIAN_FRONTEND": "noninteractive"},
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        result = subprocess.run(
            ["dpkg", "-l", package],
            capture_output=True,
        )
        return result.returncode == 0

    def update(self):
        subprocess.run(["sudo", "apt-get", "-y", "update"])


class ApkPackageManager(SystemPackageManager):
    # Alpine package names differ from Debian's; map the canonical names used at
    # call sites onto their apk equivalents.
    package_aliases = {
        "build-essential": "build-base",
        "pkg-config": "pkgconf",
        "libmariadb-dev": "mariadb-dev",
        "mariadb-server": "mariadb",
        "mariadb-client": "mariadb-client",
        "redis-server": "redis",
        "supervisor": "supervisor",
    }

    def install(self, *packages: str) -> None:
        subprocess.run(
            _privileged(["apk", "add", "--no-cache", *self._resolve(*packages)]),
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        resolved = self._resolve(package)[0]
        result = subprocess.run(
            ["apk", "info", "-e", resolved],
            capture_output=True,
        )
        return result.returncode == 0

    def update(self):
        subprocess.run(_privileged(["apk", "update"]))


class BrewPackageManager(SystemPackageManager):
    def install(self, *packages: str) -> None:
        subprocess.run(
            ["brew", "install", *packages],
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        result = subprocess.run(
            ["brew", "list", "--versions", package],
            capture_output=True,
        )
        return bool(result.stdout.strip())

    def update(self):
        return super().update()


def get_package_manager() -> SystemPackageManager:
    if is_macos():
        return BrewPackageManager()
    if is_alpine():
        return ApkPackageManager()
    return AptPackageManager()
