from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# The sandbox masks the home directory and these roots; a path a workload needs below them is bound back.
MASKED_ROOTS = (Path("/home"), Path("/root"), Path("/run"))
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
WORKLOAD_DIRECTORIES = ("sites", "config", "logs", "pids", "chromium")
# The folder in logs/ that a workload sees as its logs/. Pilot's and systemd's own files stay outside it.
WORKLOAD_LOGS = "workload"
# The bench folders that hold code. The admin and the CLI run it outside the sandbox, so a workload
# only reads it.
CODE_DIRECTORIES = ("apps", "env")


class PilotSandboxPaths:
    """Tier 1: the paths in the masked home that Pilot's own workloads need.

    Each comes from what the bench really uses, so nothing else of the user's tools is exposed.
    docs/sandbox.md explains the sandbox."""

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    @property
    def read(self) -> list[str]:
        """Read-only paths, limited to those the sandbox masks."""
        candidates = [
            self.interpreter_home,
            self.node_prefix,
            Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm"),
            Path(self.mariadb_socket_path).parent,
        ]
        return [str(path) for path in candidates if self.is_masked(path)]

    @property
    def mariadb_socket_path(self) -> str:
        from pilot.managers.database import MariaDBManager

        return MariaDBManager(self.bench.config.mariadb).socket_path

    @property
    def interpreter_home(self) -> Path:
        """Directory of the Python the bench venv links to, such as a uv-managed one."""
        return Path(os.path.realpath(self.bench.python)).parent.parent

    @staticmethod
    def create_entries(bench: Bench, declared_paths: Iterable[str] = ()) -> None:
        """Make the workload folders, the folders the apps declare in the bench, and the protected folders
        exist before the units start.

        A workload cannot create a folder in the read-only bench root, and cannot plant a protected one first."""
        for name in (*CODE_DIRECTORIES, *WORKLOAD_DIRECTORIES):
            (bench.path / name).mkdir(parents=True, exist_ok=True)
        PilotSandboxPaths.create_workload_logs(bench)
        bench_root = Path(os.path.realpath(bench.path))
        for path in map(Path, declared_paths):
            if path.is_relative_to(bench_root):
                path.mkdir(parents=True, exist_ok=True)
        for name in CONTROL_DIRECTORIES:
            (bench.config_path / name).mkdir(parents=True, exist_ok=True)
        aliases = bench.config_path / "site-aliases.json"
        if not aliases.exists():
            aliases.write_text("{}\n")
        if not (bench.config_path / "realtime-folders.txt").exists():
            bench.processes.save_realtime_folders()

    @staticmethod
    def create_workload_logs(bench: Bench) -> None:
        """A link here would mount its target as the workload's logs/, so it is refused."""
        path = bench.logs_path / WORKLOAD_LOGS
        if path.is_symlink():
            raise BenchError(f"{path} must be a folder, not a link. Remove it, then run the command again.")
        path.mkdir(exist_ok=True)

    @property
    def node_prefix(self) -> Path:
        node = shutil.which("node")
        return Path(os.path.realpath(node)).parent.parent if node else Path("/nonexistent")

    @property
    def masked_roots(self) -> tuple[Path, ...]:
        return (Path.home(), *MASKED_ROOTS)

    def is_masked(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.masked_roots)
