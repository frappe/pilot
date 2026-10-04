import os
import subprocess
from abc import ABC, abstractmethod
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.managers.platform import (
    Distro,
    _privileged,
    detect_distro,
    has_passwordless_sudo,
    is_macos,
    is_root,
)


class SystemPackageManager(ABC):
    # Canonical (Debian/apt) name -> native name(s). Unmapped names pass through.
    package_aliases: ClassVar[dict[str, str | tuple[str, ...]]] = {}
    requires_privilege: ClassVar[bool] = True

    def _resolve(self, *packages: str) -> list[str]:
        resolved: list[str] = []
        for package in packages:
            alias = self.package_aliases.get(package, package)
            names = alias if isinstance(alias, tuple) else (alias,)
            resolved.extend(name for name in names if name not in resolved)
        return resolved

    def install(self, *packages: str) -> None:
        if self.requires_privilege and not is_root() and not has_passwordless_sudo():
            names = ", ".join(self._resolve(*packages))
            raise BenchError(
                f"Required: {names}. No passwordless sudo available. Re-run install.sh as root, "
                "which installs every system package Pilot needs, or install them with your "
                "system package manager. Then re-run this command."
            )
        self._install(*packages)

    @abstractmethod
    def _install(self, *packages: str) -> None:
        """Install one or more system packages."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        """Return True if the package is already installed."""


class AptPackageManager(SystemPackageManager):
    package_aliases: ClassVar[dict[str, str | tuple[str, ...]]] = {
        "modsecurity-nginx": "libnginx-mod-http-modsecurity",
    }

    def _install(self, *packages: str) -> None:
        subprocess.run(
            _privileged(["apt-get", "install", "-y", *self._resolve(*packages)]),
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        result = subprocess.run(
            ["dpkg", "-l", package],
            capture_output=True,
        )
        return result.returncode == 0


class DnfPackageManager(SystemPackageManager):
    package_aliases: ClassVar[dict[str, str | tuple[str, ...]]] = {
        "build-essential": ("gcc", "gcc-c++", "make"),
        "pkg-config": "pkgconf-pkg-config",
        "python3-dev": "python3-devel",
        "libmariadb-dev": "mariadb-connector-c-devel",
        "libpq-dev": "libpq-devel",
        "mariadb-client": "mariadb",
        # Fedora 41+ ships valkey (redis fork) instead of redis.
        "redis-server": "valkey",
        "postgresql": "postgresql-server",
        "postgresql-client": "postgresql",
        "modsecurity-nginx": "nginx-mod-modsecurity",
    }

    def _install(self, *packages: str) -> None:
        subprocess.run(
            _privileged(["dnf", "install", "-y", *self._resolve(*packages)]),
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        resolved = self._resolve(package)
        result = subprocess.run(
            ["rpm", "-q", *resolved],
            capture_output=True,
        )
        return result.returncode == 0


class PacmanPackageManager(SystemPackageManager):
    package_aliases: ClassVar[dict[str, str | tuple[str, ...]]] = {
        "build-essential": "base-devel",
        "pkg-config": "pkgconf",
        "python3-dev": "python",
        "libmariadb-dev": "mariadb-libs",
        "libpq-dev": "postgresql-libs",
        "mariadb-server": "mariadb",
        "mariadb-client": "mariadb-clients",
        # Arch moved redis to the AUR in favour of valkey.
        "redis-server": "valkey",
        "postgresql-client": "postgresql",
        "modsecurity-nginx": "nginx-mod-modsecurity",
    }

    def _install(self, *packages: str) -> None:
        subprocess.run(
            _privileged(["pacman", "-S", "--noconfirm", "--needed", *self._resolve(*packages)]),
            check=True,
        )

    def is_installed(self, package: str) -> bool:
        resolved = self._resolve(package)
        result = subprocess.run(
            ["pacman", "-Qi", *resolved],
            capture_output=True,
        )
        return result.returncode == 0


class BrewPackageManager(SystemPackageManager):
    requires_privilege: ClassVar[bool] = False

    def _install(self, *packages: str) -> None:
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


_LINUX_PACKAGE_MANAGERS: dict[Distro, type[SystemPackageManager]] = {
    Distro.FEDORA: DnfPackageManager,
    Distro.ARCH: PacmanPackageManager,
}


def get_package_manager() -> SystemPackageManager:
    if is_macos():
        return BrewPackageManager()
    # Debian, Ubuntu, and unknown distros all fall back to apt.
    manager = _LINUX_PACKAGE_MANAGERS.get(detect_distro(), AptPackageManager)
    return manager()
