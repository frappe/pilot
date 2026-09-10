from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.managers.platform import add_mysqlclient_flags, is_macos, which
from pilot.managers.python_assets import PythonAssetBuilder
from pilot.utils import get_yarn_bin, run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench

__all__ = ["AdminEnvManager", "PythonEnvManager", "ensure_uv", "find_uv"]


def find_uv() -> str | None:
    """uv on PATH, or wherever its installers put it.

    A service PATH rarely carries ~/.local/bin, which is exactly where the uv
    installer writes, so asking PATH alone reports a working uv as missing.
    """
    if uv := which("uv"):
        return uv
    installed = (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv")
    return next((str(path) for path in installed if path.exists()), None)


def ensure_uv() -> str:
    """Path to uv, installing it first when the host has none."""
    if uv := find_uv():
        return uv

    print("uv not found - installing via official installer...", flush=True)
    try:
        run_command(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"], stream_output=True)
    except Exception:
        print("curl installer failed - falling back to pip install uv...", flush=True)
        run_command([sys.executable, "-m", "pip", "install", "--user", "uv"], stream_output=True)

    if uv := find_uv():
        return uv

    raise BenchError("uv was installed but cannot be found. Add ~/.local/bin to your PATH and re-run.")


class AdminEnvManager:
    """Owns the source-tree admin venv at <cli_root>/.admin-venv."""

    def __init__(self, cli_root: Path) -> None:
        self.venv_path = cli_root / ".admin-venv"

    @property
    def python(self) -> Path:
        return self.venv_path / "bin" / "python"

    @property
    def gunicorn(self) -> Path:
        return self.venv_path / "bin" / "gunicorn"

    @property
    def uv(self) -> str:
        uv = find_uv()
        if not uv:
            raise RuntimeError("uv not found - run the Pilot install script to set it up")
        return uv

    def ensure(self) -> None:
        """Create the admin venv and install admin dependencies when they change."""
        self._ensure_venv()
        self.install_python_deps()
        self._ensure_frontend_deps()

    def _ensure_venv(self) -> bool:
        if self.python.exists():
            return False
        print("Setting up admin environment (one-time)...")
        print("  Creating virtual environment...", end=" ", flush=True)
        subprocess.run([self.uv, "venv", str(self.venv_path)], check=True)
        print("done")
        return True

    def install_python_deps(self) -> None:
        """Install admin Python dependencies when the declared set changed."""
        self._ensure_venv()
        deps = self._read_admin_deps()
        if not deps:
            print("  No admin dependencies specified, skipping installation.")
            return

        installed = self.venv_path / ".admin-deps"
        if installed.exists() and installed.read_text().splitlines() == deps:
            return

        print(f"  Installing {', '.join(deps)}...", end=" ", flush=True)
        subprocess.run(
            [self.uv, "pip", "install", "--python", str(self.python), "--quiet", *deps], check=True
        )
        installed.write_text("\n".join(deps))
        print("done")

    def _ensure_frontend_deps(self) -> None:
        from pilot import is_dev_build

        frontend = self.venv_path.parent / "admin" / "frontend"
        # Releases ship the source but serve the prebuilt dist, so they skip its Node deps.
        if not is_dev_build or not (frontend / "package.json").exists():
            return
        if (frontend / "node_modules").exists():
            return
        print("  Installing admin frontend Node.js dependencies...", flush=True)
        subprocess.run(["npm", "install"], cwd=frontend, check=True)
        print("  done")

    def _read_admin_deps(self) -> list[str]:
        pyproject = self.venv_path.parent / "pyproject.toml"
        if not pyproject.exists():
            return [
                "flask>=3.0",
                "psutil>=5.9",
                "pymysql>=1.1",
                "gunicorn>=21.2",
                "pyjwt[crypto]>=2.8",
            ]
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("optional-dependencies", {}).get("admin")


class PythonEnvManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def _assets(self) -> PythonAssetBuilder:
        return PythonAssetBuilder(self)

    def ensure_python(self) -> None:
        pass

    def create_venv(self) -> None:
        if self.bench.python.exists():
            return
        uv = ensure_uv()
        version = self.bench.config.python_version
        run_command([uv, "venv", "--python", version, str(self.bench.env_path)], stream_output=True)

    def _build_env(self) -> dict:
        """Build subprocess env with yarn on PATH and macOS mysqlclient flags."""
        env = os.environ.copy()

        try:
            yarn_dir = str(Path(get_yarn_bin()).parent)
            env["PATH"] = os.pathsep.join([yarn_dir, env.get("PATH", "")])
        except BenchError:
            pass  # yarn not installed yet (e.g. compiling C extensions pre-node)

        add_mysqlclient_flags(env)

        return env

    def install_app(self, app: "App") -> None:
        uv = ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command(
            [uv, "pip", "install", "--python", python, "-e", app.editable_target],
            stream_output=True,
            env=self._build_env(),
        )

    def uninstall_app(self, app_name: str) -> None:
        uv = ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "uninstall", "--python", python, app_name], stream_output=True)

    def install_node(self) -> None:
        if not which("node"):
            self._install_node()
        if not which("yarn"):
            self._install_yarn()

    def _install_node(self) -> None:
        if is_macos():
            run_command(["brew", "install", "node"])
            return
        raise BenchError(
            "Node.js is not installed. Re-run install.sh as root to install it, or install it yourself."
        )

    def _install_yarn(self) -> None:
        if is_macos():
            run_command(["npm", "install", "-g", "yarn"])
        else:
            npm_prefix = Path.home() / ".local"
            npm_prefix.mkdir(parents=True, exist_ok=True)
            run_command(["npm", "install", "-g", "yarn", "--prefix", str(npm_prefix)])

    def install_node_dependencies(self) -> None:
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                run_command(
                    [get_yarn_bin(), "install", "--frozen-lockfile"],
                    cwd=app.path,
                    stream_output=True,
                )

    def build_assets(self) -> None:
        self._assets.build_assets()

    def build_assets_for_app(self, app: "App", force: bool = False) -> None:
        self._assets.build_assets_for_app(app, force=force)
