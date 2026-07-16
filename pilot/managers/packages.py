"""System package installation for every supported platform.

Call sites pass canonical Debian/apt package names; each manager's
``package_aliases`` maps those onto its distro's own names. An alias may be
a tuple when one canonical package corresponds to several native ones.
"""

import os
import subprocess
from abc import ABC, abstractmethod

from pilot.managers.platform import Distro, _privileged, detect_distro, is_macos


class SystemPackageManager(ABC):
    # Canonical (Debian/apt) name -> native name(s). Unmapped names pass through.
    package_aliases: dict[str, str | tuple[str, ...]] = {}

    def _resolve(self, *packages: str) -> list[str]:
        resolved: list[str] = []
        for package in packages:
            alias = self.package_aliases.get(package, package)
            names = alias if isinstance(alias, tuple) else (alias,)
            resolved.extend(name for name in names if name not in resolved)
        return resolved

    @abstractmethod
    def install(self, *packages: str) -> None:
        """Install one or more system packages."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        """Return True if the package is already installed."""

    @abstractmethod
    def update(self) -> None:
        """Refresh the package index."""


class AptPackageManager(SystemPackageManager):
    package_aliases = {
        "modsecurity-nginx": "libnginx-mod-http-modsecurity",
    }

    def install(self, *packages: str) -> None:
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

    def update(self):
        subprocess.run(_privileged(["apt-get", "-y", "update"]))


class DnfPackageManager(SystemPackageManager):
    package_aliases = {
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
        "zfsutils-linux": "zfs",
        "modsecurity-nginx": "nginx-mod-modsecurity",
    }

    def install(self, *packages: str) -> None:
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

    def update(self):
        subprocess.run(_privileged(["dnf", "-y", "makecache"]))


class PacmanPackageManager(SystemPackageManager):
    package_aliases = {
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
        # Only available via the third-party archzfs repository.
        "zfsutils-linux": "zfs-utils",
        "modsecurity-nginx": "nginx-mod-modsecurity",
    }

    def install(self, *packages: str) -> None:
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

    def update(self):
        subprocess.run(_privileged(["pacman", "-Sy", "--noconfirm"]))


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
