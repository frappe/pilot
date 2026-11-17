from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.server.ssh_keys import (
    AuthorizedKeysStore,
    InvalidSSHKeyError,
    LastSSHKeyError,
    SSHKey,
    SSHKeyAlreadyExistsError,
    SSHKeyNotFoundError,
)

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.bench.directory import BenchDirectory
    from pilot.core.server.auto_update import AutoUpdate
    from pilot.core.server.legacy_bench import LegacyBenchImporter


class Server:
    """The host Pilot runs on: its installation folder, benches and SSH keys."""

    @property
    def root(self) -> Path:
        """The folder Pilot is installed in, which holds the pilot package and benches/."""
        import pilot as package

        return Path(package.__file__).parent.parent

    @property
    def benches_dir(self) -> Path:
        """The folder that holds every bench on this host."""
        return self.root / "benches"

    @property
    def benches(self) -> "BenchDirectory":
        """Every bench under the benches folder, with its config."""
        from pilot.core.bench.directory import BenchDirectory

        return BenchDirectory(self.benches_dir)

    @property
    def bench_paths(self) -> list[Path]:
        """Every bench directory under the benches directory, by name."""
        return sorted(path.parent for path in self.benches_dir.glob("*/bench.toml"))

    @property
    def auto_update(self) -> "AutoUpdate":
        """The Pilot auto update."""
        from pilot.core.server.auto_update import AutoUpdate

        return AutoUpdate(self)

    def bench(self, path_or_name: str | Path) -> "Bench":
        """The bench at a path, or with a name under the benches directory."""
        from pilot.core.bench import Bench

        path = Path(path_or_name).expanduser()
        if isinstance(path_or_name, str) and not path.is_absolute() and path.parent == Path("."):
            path = self.benches_dir / path
        return Bench(path)

    def get_legacy_bench_importer(self, path: str | Path, name: str = "") -> "LegacyBenchImporter":
        """An importer for a bench made by the `bench` CLI, named `name` or after its folder."""
        from pilot.core.server.legacy_bench import LegacyBench, LegacyBenchImporter

        return LegacyBenchImporter(LegacyBench(path), self.benches_dir, name)

    @property
    def ssh_keys(self) -> AuthorizedKeysStore:
        """The SSH authorized keys."""
        return AuthorizedKeysStore()


__all__ = [
    "AuthorizedKeysStore",
    "InvalidSSHKeyError",
    "LastSSHKeyError",
    "SSHKey",
    "SSHKeyAlreadyExistsError",
    "SSHKeyNotFoundError",
    "Server",
]
