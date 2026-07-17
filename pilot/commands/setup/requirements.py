from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class SetupRequirementsCommand(Command):
    name: ClassVar[str] = "requirements"
    help: ClassVar[str] = "Install Python and JS requirements for all apps."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        self._install_python()
        self._install_js()

    def _install_python(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager
        from pilot.utils import run_command

        manager = PythonEnvManager(self.bench)
        uv = manager._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")

        for app in self.bench.apps():
            if not (app.path / "pyproject.toml").exists() and not (app.path / "setup.py").exists():
                continue
            self.print(f"Installing Python requirements for {app.config.name}...")
            run_command(
                [uv, "pip", "install", "--python", python, "-e", str(app.path)],
                stream_output=True,
            )

    def _install_js(self) -> None:
        from pilot.utils import get_yarn_bin, run_command

        for app in self.bench.apps():
            if not (app.path / "package.json").exists():
                continue
            self.print(f"Installing JS requirements for {app.config.name}...")
            run_command([get_yarn_bin(), "install"], cwd=app.path, stream_output=True)
