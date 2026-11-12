from __future__ import annotations

import socket
from pathlib import Path


class DiskUsage:
    """Disk space a directory tree takes."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @property
    def bytes(self) -> int:
        """Allocated bytes; 0 for a missing path, which is legitimately empty."""
        from pilot.internal.command import run_command

        if not self.path.exists():
            return 0
        # `du -sk` is POSIX; the GNU-only `-b` fails on BSD and macOS.
        return int(run_command(["du", "-sk", str(self.path)], timeout=10).stdout.split()[0]) * 1024


class LocalPort:
    """A TCP port on 127.0.0.1."""

    def __init__(self, number: int) -> None:
        self.number = number

    @property
    def is_listening(self) -> bool:
        """Whether something accepts connections on it now."""
        try:
            with socket.create_connection(("127.0.0.1", self.number), timeout=0.2):
                return True
        except OSError:
            return False

    def get_first_free(self) -> int:
        """This port or the first free one after it. macOS keeps the port as it is."""
        from pilot.managers.platform import HostPlatform

        if HostPlatform().is_macos:
            return self.number
        number = self.number
        while LocalPort(number).is_listening:
            number += 1
        return number


class InstalledPackages:
    """Packages installed in a Python environment, read from their metadata files."""

    def __init__(self, env_path: Path) -> None:
        self.env_path = env_path

    def get_version(self, name: str) -> str:
        """The installed version of a package, or "" when it is not installed. No subprocess."""
        lib_dir = self.env_path / "lib"
        if not lib_dir.is_dir():
            return ""
        normalized = name.replace("-", "_")
        for python_dir in lib_dir.iterdir():
            site_packages = python_dir / "site-packages"
            if not site_packages.is_dir():
                continue
            for dist_info in site_packages.glob(f"{normalized}-*.dist-info"):
                metadata = dist_info / "METADATA"
                if not metadata.exists():
                    continue
                for line in metadata.read_text(errors="replace").splitlines():
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
        return ""
