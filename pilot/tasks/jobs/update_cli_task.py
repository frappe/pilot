from __future__ import annotations

import subprocess

from pilot.loader import cli_root

from .base_task import BaseTask


class UpdateCliTask(BaseTask):
    def run(self) -> None:
        root = cli_root()
        self._step("update", f"Update bench-cli at {root}")
        subprocess.run(["git", "-C", str(root), "pull"], check=True)
        self._step("done")


if __name__ == "__main__":
    UpdateCliTask.main()
