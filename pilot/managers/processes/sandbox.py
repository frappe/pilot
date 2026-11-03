from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# The home directory is masked; these hold the rest of the user's home on other layouts.
MASKED_ROOTS = (Path("/home"), Path("/root"))
WAITER = Path("pilot") / "internal" / "wait_for_socket.py"


def get_masked_roots() -> tuple[Path, ...]:
    return (Path.home(), *MASKED_ROOTS)


class PilotSandboxPaths:
    """Tier 1: the paths in the masked home that Pilot's own workloads need.

    Each comes from what the bench really uses, so nothing else of the user's tools is exposed."""

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    @property
    def read(self) -> list[str]:
        """Read-only paths, limited to those the sandbox masks."""
        candidates = [
            self.get_interpreter_home(),
            self.get_node_prefix(),
            Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm"),
            Path(self.mariadb_socket_path).parent,
            cli_root() / WAITER,
        ]
        return [str(path) for path in candidates if self.is_masked(path)]

    @property
    def mariadb_socket_path(self) -> str:
        from pilot.managers.database import MariaDBManager

        return MariaDBManager(self.bench.config.mariadb).socket_path

    def get_interpreter_home(self) -> Path:
        """Directory of the Python the bench venv links to, such as a uv-managed one."""
        return Path(os.path.realpath(self.bench.python)).parent.parent

    @staticmethod
    def get_node_prefix() -> Path:
        node = shutil.which("node")
        return Path(os.path.realpath(node)).parent.parent if node else Path("/nonexistent")

    @staticmethod
    def is_masked(path: Path) -> bool:
        return any(path.is_relative_to(root) for root in get_masked_roots())
