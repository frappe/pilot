from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.utils import get_yarn_bin, run_command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class SetupRequirementsCommand:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        self._install_python()
        self._install_js()

    def _install_python(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        uv = manager._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")

        for app in self.bench.apps():
            if not (app.path / "pyproject.toml").exists() and not (app.path / "setup.py").exists():
                continue
            print(f"Installing Python requirements for {app.config.name}...")
            run_command(
                [uv, "pip", "install", "--python", python, "-e", str(app.path)],
                stream_output=True,
            )

    def _install_js(self) -> None:
        for app in self.bench.apps():
            if not (app.path / "package.json").exists():
                continue
            print(f"Installing JS requirements for {app.config.name}...")
            run_command([get_yarn_bin(), "install"], cwd=app.path, stream_output=True)
