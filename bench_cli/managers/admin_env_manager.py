from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import tomllib


class AdminEnvManager:
    """
    Manages an isolated venv at <cli_root>/.admin-venv/ that contains Flask.
    Created on first use; subsequent calls are instant (venv already exists).
    """

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
            raise RuntimeError("uv not found — run the bench-cli install script to set it up")
        return uv

    def ensure(self) -> None:
        """Create the admin venv and install admin dependencies if not already done."""
        if self._ensure_venv():
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
        """Install any missing admin Python dependencies into the admin venv."""
        self._ensure_venv()
        deps = self._read_admin_deps()
        if not deps:
            print("  No admin dependencies specified, skipping installation.")
            return

        print(f"  Installing {', '.join(deps)}...", end=" ", flush=True)
        subprocess.run([self.uv, "pip", "install", "--python", str(self.python), "--quiet", *deps], check=True)
        print("done")

    def _ensure_frontend_deps(self) -> None:
        """
        Install admin frontend Node.js dependencies (needed for the vite dev server).
        """
        frontend = self.venv_path.parent / "admin" / "frontend"
        if not (frontend / "package.json").exists():
            return  # not running from the bench-cli source tree
        if (frontend / "node_modules").exists():
            return
        print("  Installing admin frontend Node.js dependencies...", flush=True)
        subprocess.run(["npm", "install"], cwd=frontend, check=True)
        print("  done")

    def _read_admin_deps(self) -> list[str]:
        pyproject = self.venv_path.parent / "pyproject.toml"
        if not pyproject.exists():
            return ["flask>=3.0", "psutil>=5.9", "pymysql>=1.1", "gunicorn>=21.2"]
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("optional-dependencies", {}).get("admin")
