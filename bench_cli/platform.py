import os
import platform
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path


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


def _privileged(command: list[str]) -> list[str]:
    """Prefix a command with sudo unless we are already root.

    Alpine images commonly run as root without sudo installed, so dropping the
    prefix when euid is 0 keeps installs and service calls working out of the box.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return command
    return ["sudo", *command]


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


def service_running(name: str) -> bool:
    """Return True if the named system service is currently running."""
    if is_alpine():
        argv = ["rc-service", name, "status"]
    else:
        argv = ["systemctl", "is-active", "--quiet", name]
    return subprocess.run(argv, capture_output=True).returncode == 0


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
    package_aliases = {
        "build-essential": "build-base",
        "pkg-config": "pkgconf",
        "libmariadb-dev": "mariadb-dev",
        "redis-server": "redis",
    }

    def install(self, *packages: str) -> None:
        subprocess.run(
            _privileged(["apk", "add", *self._resolve(*packages)]),
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
