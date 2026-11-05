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
# What Pilot keeps in <bench>/config for the admin, which loads and runs it unsandboxed. A workload
# must not change these. Everything else in config/ stays writable, because Frappe creates its
# global lock files there. A test checks this list against every config path Pilot uses.
CONTROL_DIRECTORIES = (
    "services",
    "nginx",
    "modsecurity",
    "letsencrypt",
    "frappe_cloud",
    "site-renames",
    "site-creations",
)
CONTROL_FILES = (
    "site-aliases.json",
    "realtime-folders.txt",
    "gunicorn.conf.py",
    "admin-gunicorn.conf.py",
    "Procfile",
    "redis_cache.conf",
    "redis_queue.conf",
)
CONTROL_ENTRIES = (*CONTROL_DIRECTORIES, *CONTROL_FILES)
# The bench folders a workload uses. The rest of the bench root, such as bench.toml, .credentials and the
# admin's tasks/, stays outside the sandbox. Folders, because Linux detaches a file mount once Pilot
# replaces the file by rename.
WORKLOAD_DIRECTORIES = ("apps", "env", "sites", "config", "logs", "pids", "chromium", "flow-server")


def create_sandbox_entries(bench: Bench) -> None:
    """Make the workload folders and the protected folders exist before the units start.

    A workload cannot create a folder in the read-only bench root, and cannot plant a protected one first."""
    for name in WORKLOAD_DIRECTORIES:
        (bench.path / name).mkdir(parents=True, exist_ok=True)
    for name in CONTROL_DIRECTORIES:
        (bench.config_path / name).mkdir(parents=True, exist_ok=True)
    aliases = bench.config_path / "site-aliases.json"
    if not aliases.exists():
        aliases.write_text("{}\n")
    if not (bench.config_path / "realtime-folders.txt").exists():
        bench.processes.save_realtime_folders()


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
