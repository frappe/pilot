from __future__ import annotations

from contextlib import contextmanager
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


class Server:
    @property
    def benches_dir(self) -> Path:
        from pilot.utils import benches_dir

        return benches_dir()

    def bench(self, path_or_name: str | Path) -> "Bench":
        from pilot.core.bench import Bench

        path = Path(path_or_name).expanduser()
        if isinstance(path_or_name, str) and not path.is_absolute() and path.parent == Path("."):
            path = self.benches_dir / path
        return Bench(path)

    @property
    def ssh_keys(self) -> AuthorizedKeysStore:
        return AuthorizedKeysStore()

    @contextmanager
    def build_action_lock(self):
        """Serialize asset builds host-wide, including across benches. Builds are
        the largest memory consumer on a host; two at once outgrow any one budget."""
        from pilot.exceptions import BenchError
        from pilot.internal.atomic_file import exclusive_file_lock

        benches_dir = self.benches_dir
        benches_dir.mkdir(parents=True, exist_ok=True)
        try:
            with exclusive_file_lock(benches_dir / "build-action", blocking=False):
                yield
        except BlockingIOError as exc:
            raise BenchError("Another build is already running on this server.") from exc


__all__ = [
    "AuthorizedKeysStore",
    "InvalidSSHKeyError",
    "LastSSHKeyError",
    "SSHKey",
    "SSHKeyAlreadyExistsError",
    "SSHKeyNotFoundError",
    "Server",
]
