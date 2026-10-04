from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class ListWorktreesCommand(Command):
    name: ClassVar[str] = "list"
    help: ClassVar[str] = "List the bench's worktrees."
    group: ClassVar[str] = "worktree"

    def run(self) -> None:
        worktrees = self.bench.worktrees()
        if not worktrees:
            self.report("No worktrees. Add one with: pilot worktree add APP NAME")
            return
        for worktree in worktrees:
            config = worktree.config
            host = worktree.site_name
            vite = f"vite=http://{host}:{worktree.frontend_port}  " if worktree.frontend_path else ""
            self.report(
                f"{config.name}  app={config.app}  branch={worktree.branch or '-'}  site={host}  "
                f"web=http://{host}:{worktree.runtime_bench.config.http_port}  {vite}"
                f"{'running' if worktree.is_running else 'stopped'}"
            )
