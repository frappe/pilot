from __future__ import annotations

import random
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

    def get_rollout_number(self) -> int:
        """This host's place, from 1 to 100, in every app update rollout. Chosen on first use."""
        from pilot.exceptions import BenchError
        from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
        from pilot.utils import cli_root

        path = cli_root() / "system" / "rollout-number"
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(path):
            try:
                number = int(path.read_text())
            except FileNotFoundError:
                number = random.randint(1, 100)
                replace_private_text_locked(path, str(number))
                return number
            except ValueError:
                number = 0

            if not 1 <= number <= 100:
                raise BenchError(
                    f"The rollout number in {path} is damaged. Set it to a number from 1 to 100, "
                    "or delete the file so Pilot picks a new one."
                )

            return number


__all__ = [
    "AuthorizedKeysStore",
    "InvalidSSHKeyError",
    "LastSSHKeyError",
    "SSHKey",
    "SSHKeyAlreadyExistsError",
    "SSHKeyNotFoundError",
    "Server",
]
