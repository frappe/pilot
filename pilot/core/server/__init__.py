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
    from pilot.core.server.auto_update import AutoUpdate


class Server:
    @property
    def benches_dir(self) -> Path:
        from pilot.utils import benches_dir

        return benches_dir()

    @property
    def bench_paths(self) -> list[Path]:
        """Every bench directory under the benches directory, by name."""
        return sorted(path.parent for path in self.benches_dir.glob("*/bench.toml"))

    @property
    def auto_update(self) -> "AutoUpdate":
        from pilot.core.server.auto_update import AutoUpdate

        return AutoUpdate(self)

    def bench(self, path_or_name: str | Path) -> "Bench":
        from pilot.core.bench import Bench

        path = Path(path_or_name).expanduser()
        if isinstance(path_or_name, str) and not path.is_absolute() and path.parent == Path("."):
            path = self.benches_dir / path
        return Bench(path)

    @property
    def ssh_keys(self) -> AuthorizedKeysStore:
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
