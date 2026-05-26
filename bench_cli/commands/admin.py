from __future__ import annotations

from pathlib import Path

from bench_cli.exceptions import BenchError
from bench_cli.utils import run_command


def _cli_root() -> Path:
    import bench_cli as _pkg
    return Path(_pkg.__file__).parent.parent


class BuildAdminCommand:
    def run(self) -> None:
        frontend = self._find_frontend()
        print(f"Building admin frontend at {frontend}...")
        if not (frontend / "node_modules").exists():
            print("Running npm install...")
            run_command(["npm", "install"], cwd=frontend, stream_output=True)
        run_command(["npm", "run", "build"], cwd=frontend, stream_output=True)
        print("\nAdmin frontend rebuilt successfully.")

    def _find_frontend(self) -> Path:
        candidate = _cli_root() / "admin" / "frontend"
        if (candidate / "package.json").exists():
            return candidate
        raise BenchError(
            "admin/frontend not found. "
            "This command requires the bench-cli source directory with admin/frontend/."
        )
