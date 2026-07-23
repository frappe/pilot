from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.managers.platform import is_macos, which
from pilot.managers.python_assets import PythonAssetBuilder
from pilot.utils import get_yarn_bin, run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench

__all__ = ["AdminEnvManager", "PythonEnvManager"]


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
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv not found - run the bench-cli install script to set it up")
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
        # Only dev builds compile the frontend; releases serve the prebuilt dist
        # and never need its Node deps, even though the source ships with them.
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
        uv = self._ensure_uv()
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

        if is_macos():
            self._add_mysqlclient_flags(env)

        return env

    def _add_mysqlclient_flags(self, env: dict) -> None:
        config_bin = self._mariadb_config_bin()
        if not config_bin:
            return
        try:
            env.setdefault(
                "MYSQLCLIENT_CFLAGS",
                subprocess.run(
                    [config_bin, "--cflags"], capture_output=True, text=True, check=True, timeout=5
                ).stdout.strip(),
            )
            env.setdefault(
                "MYSQLCLIENT_LDFLAGS",
                subprocess.run(
                    [config_bin, "--libs"], capture_output=True, text=True, check=True, timeout=5
                ).stdout.strip(),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _mariadb_config_bin() -> str | None:
        """Find mariadb_config/mysql_config, including keg-only Homebrew installs."""
        if found := (shutil.which("mariadb_config") or shutil.which("mysql_config")):
            return found
        try:
            prefix = subprocess.run(
                ["brew", "--prefix", "mariadb"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None
        candidate = Path(prefix) / "bin" / "mariadb_config"
        return str(candidate) if candidate.exists() else None

    def install_app(self, app: "App") -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command(
            [uv, "pip", "install", "--python", python, "-e", str(app.path)],
            stream_output=True,
            env=self._build_env(),
        )

    def uninstall_app(self, app_name: str) -> None:
        uv = self._ensure_uv()
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

    def build_assets_for_app(self, app: "App") -> None:
        self._assets.build_assets_for_app(app)

    def _ensure_yarn_install(self, path: Path) -> None:
        self._assets.ensure_yarn_install(path)

    def _try_download_prebuilt_assets(self, app: "App", app_public_dir: Path, dist_dir: Path) -> bool:
        return self._assets.try_download_prebuilt_assets(app, app_public_dir, dist_dir)

    @staticmethod
    def _release_asset_url(app: "App", branch: str) -> str | None:
        return PythonAssetBuilder.release_asset_url(app, branch)

    @staticmethod
    def _download_and_extract(url: str, dest_dir: Path) -> bool:
        return PythonAssetBuilder.download_and_extract(url, dest_dir)

    def _has_prebuilt_assets(self, dist_dir: Path) -> bool:
        return PythonAssetBuilder.has_prebuilt_assets(dist_dir)

    def _setup_prebuilt_assets(self, app_name: str, app_public_dir: Path, dist_dir: Path) -> None:
        self._assets.setup_prebuilt_assets(app_name, app_public_dir, dist_dir)

    def _write_assets_json(self, app_name: str, dist_dir: Path, assets_dir: Path) -> None:
        self._assets.write_assets_json(app_name, dist_dir, assets_dir)

    @staticmethod
    def _merge_json(path: Path, new_entries: dict) -> None:
        PythonAssetBuilder.merge_json(path, new_entries)

    def _ensure_uv(self) -> str:
        uv = shutil.which("uv")
        if uv:
            return uv

        print("uv not found - installing via official installer...", flush=True)
        try:
            run_command(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                stream_output=True,
            )
        except Exception:
            print("curl installer failed - falling back to pip install uv...", flush=True)
            run_command(
                [sys.executable, "-m", "pip", "install", "--user", "uv"],
                stream_output=True,
            )

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

        raise BenchError("uv was installed but cannot be found. Add ~/.local/bin to your PATH and re-run.")
