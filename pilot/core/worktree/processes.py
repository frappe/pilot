from __future__ import annotations

import os
import shlex
import signal
import time
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.tasks.process_identity import ProcessInspector
from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager
from pilot.utils import get_yarn_bin

if TYPE_CHECKING:
    from pilot.core.worktree import Worktree

# The runner drains the workload, then redis, each within its own grace period.
_STOP_WAIT_SECONDS = 30
_FRONTEND_HOST = "127.0.0.1"


class WorktreeProcessManager(ProcessManager):
    """Runs a worktree's development processes from its overlay bench.

    No admin plane, its own redis, and a stop that only signals the worktree's own runner."""

    def __init__(self, worktree: "Worktree") -> None:
        super().__init__(worktree.runtime_bench, watch_admin_js=False)
        self.worktree = worktree

    def write_config(self) -> None:
        """Procfile and redis configs only: no admin env and no gunicorn config."""
        self._ensure_redis_config()
        lines = [f"{pd.name}: {shlex.join(pd.argv)}\n" for pd in self._process_definitions()]
        self.procfile_path.write_text("".join(lines))

    def is_running(self) -> bool:
        pid = self._runner_pid()
        return pid is not None and _is_runner(pid, self.worktree.config.name)

    def stop(self) -> None:
        """Signal the runner named by the pid file and wait for it. Nothing else is touched:
        the base class falls back to killing whatever listens on the bench's ports.
        A runner that died abruptly leaves its pid file, and the system can reuse that pid,
        so a pid whose command line is not this worktree's runner is never signalled."""
        pid = self._runner_pid()
        self.pid_file.unlink(missing_ok=True)
        if pid is None or not _is_runner(pid, self.worktree.config.name):
            raise BenchError(f"Worktree '{self.worktree.config.name}' is not running.")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            raise BenchError(f"Worktree '{self.worktree.config.name}' is not running.") from None
        deadline = time.monotonic() + _STOP_WAIT_SECONDS
        while _is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)

    def _runner_pid(self) -> int | None:
        try:
            return int(self.pid_file.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _process_definitions(self) -> list[ProcessDefinition]:
        builder = self._definitions
        definitions = [builder.to_dev(pd) for pd in builder.prod_process_definitions() if pd.name != "admin"]
        if self.bench.config.watch_apps_js and self.worktree.has_asset_bundles:
            definitions.append(self.watch_definition(builder))
        if self.worktree.frontend_path is not None:
            definitions.append(self.frontend_definition())
        for pd in definitions:
            if pd.name == "socketio" and pd.argv[0] == "node":
                pd.argv = self.socketio_argv()
            pd.env = {**pd.env, **self.worktree.env}
        return definitions

    def socketio_argv(self) -> list[str]:
        """Keep node on the overlay's frappe link, so it reads the overlay's config and modules."""
        socketio = self.bench.apps_path / "frappe" / "socketio.js"
        return ["node", "--preserve-symlinks", "--preserve-symlinks-main", str(socketio)]

    def watch_definition(self, builder: ProcessDefinitionBuilder) -> ProcessDefinition:
        """Watch only the worktree app, and never through a path that reaches main's files.

        `frappe watch` also runs the page-island watcher, which writes to `sites/assets/frappe`.
        That is the worktree only when the worktree is frappe; other apps run esbuild directly."""
        if self.worktree.config.app == "frappe":
            definition = builder.watch_definition()
            definition.argv = [*definition.argv, "--apps", "frappe"]
            return definition
        return ProcessDefinition(
            name="watch",
            argv=[get_yarn_bin(), "run", "watch", "--apps", self.worktree.config.app],
            log_file=self.bench.logs_path / "watch.log",
            working_dir=self.worktree.frappe_source_path,
            critical=False,
        )

    def frontend_definition(self) -> ProcessDefinition:
        """The app's Vite dev server. frappe-ui picks port 8080 + offset, so a taken port must fail."""
        return ProcessDefinition(
            name="frontend",
            argv=[get_yarn_bin(), "dev", "--strictPort", "--host", _FRONTEND_HOST],
            log_file=self.bench.logs_path / "frontend.log",
            working_dir=self.worktree.frontend_path,
            critical=False,
        )


def _is_runner(pid: int, name: str) -> bool:
    """Whether pid runs `pilot ... worktree start ... NAME`, the command that writes the pid file."""
    try:
        argv = ProcessInspector().command_line(pid).split()
    except OSError:
        return False
    return any(argv[i : i + 2] == ["worktree", "start"] and name in argv[i + 2 :] for i in range(len(argv)))


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
