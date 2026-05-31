from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.exceptions import BenchError
from bench_cli.platform import is_macos
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.core.app import App
    from bench_cli.core.bench import Bench


class PythonEnvManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def ensure_python(self) -> None:
        # uv manages Python discovery and download at venv-creation time.
        pass

    def create_venv(self) -> None:
        if self.bench.python.exists():
            return
        uv = self._ensure_uv()
        version = self.bench.config.python_version
        run_command([uv, "venv", "--python", version, str(self.bench.env_path)], stream_output=True)

    def install_app(self, app: "App") -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "install", "--python", python, "-e", str(app.path)], stream_output=True)

    def uninstall_app(self, app_name: str) -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "uninstall", "--python", python, app_name], stream_output=True)

    def install_node(self) -> None:
        if shutil.which("node"):
            if not shutil.which("yarn"):
                run_command(["npm", "install", "-g", "yarn"])
            return
        if is_macos():
            run_command(["brew", "install", "node"])
        else:
            self._install_node_linux()
        run_command(["npm", "install", "-g", "yarn"])

    def install_node_dependencies(self) -> None:
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                run_command(["npm", "install"], cwd=app.path, stream_output=True)

    def build_assets(self) -> None:
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path, stream_output=True,
        )

    def _ensure_uv(self) -> str:
        """Return path to uv, installing it if not on PATH."""
        uv = shutil.which("uv")
        if uv:
            return uv

        print("uv not found — installing via official installer...", flush=True)
        try:
            run_command(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                stream_output=True,
            )
        except Exception:
            print("curl installer failed — falling back to pip install uv...", flush=True)
            run_command(
                [sys.executable, "-m", "pip", "install", "--user", "uv"],
                stream_output=True,
            )

        # The installer typically puts uv in ~/.local/bin (Linux/macOS).
        for candidate in [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]:
            if candidate.exists():
                return str(candidate)

        # Re-check PATH in case the shell profile was updated.
        uv = shutil.which("uv")
        if uv:
            return uv

        raise BenchError(
            "uv was installed but cannot be found. "
            "Add ~/.local/bin to your PATH and re-run."
        )

    def _install_node_linux(self) -> None:
        run_command(
            ["bash", "-c", "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"],
            stream_output=True,
        )
        run_command(["sudo", "apt-get", "install", "-y", "nodejs"], stream_output=True)
