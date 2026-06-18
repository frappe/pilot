import os
import platform
import shutil
import subprocess
from abc import ABC, abstractmethod
from enum import Enum

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


def has_passwordless_sudo() -> bool:
    """True if sudo runs without prompting for a password (non-interactive)."""
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


class SystemPackageManager(ABC):
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
    return AptPackageManager()
