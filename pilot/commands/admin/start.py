from __future__ import annotations

import argparse
from pathlib import Path

from pilot.archive import extract_tar_archive
from pilot.commands.base import Command
from pilot.exceptions import BenchError

_ADMIN_RELEASE_URL = "https://github.com/frappe/bench-cli/releases/download/latest-build/admin-frontend.tar.gz"
# The frontend toolchain (unplugin via frappe-ui/vite) uses import.meta.dirname,
# which only exists in Node 20.11+. Older Node fails the build with an opaque
# "paths[0] ... undefined" error, so we check up-front.
_MIN_NODE = (20, 11)


def download_admin_frontend(cli_root: Path) -> bool:
    """Download and extract the pre-built admin frontend. Returns True on success."""
    import tempfile
    import urllib.error
    import urllib.request

    static_dir = cli_root / "admin" / "backend" / "static"
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp = Path(tmp_file.name)

    print("Downloading admin frontend from GitHub release...", flush=True)
    try:
        urllib.request.urlretrieve(_ADMIN_RELEASE_URL, tmp)
    except urllib.error.URLError as e:
        print(f"  Download failed: {e}", flush=True)
        tmp.unlink(missing_ok=True)
        return False

    try:
        static_dir.mkdir(parents=True, exist_ok=True)
        extract_tar_archive(tmp, static_dir)
        print("  Admin frontend downloaded successfully.", flush=True)
        return True
    except Exception as e:
        print(f"  Extraction failed: {e}", flush=True)
        return False
    finally:
        tmp.unlink(missing_ok=True)


class BuildAdminCommand(Command):
    name = "build-admin"
    help = "Download or rebuild admin frontend assets."
    requires_bench = False

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true", help="Skip download and build from source.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(force_build=args.force)

    def __init__(self, force_build: bool = False) -> None:
        self.force_build = force_build

    def run(self) -> None:
        from pilot.loader import cli_root
        from pilot.utils import run_command

        if not self.force_build and download_admin_frontend(cli_root()):
            return
        if self.force_build:
            print("Skipping download, building from source...")
        else:
            print("Download failed, building from source...")
        frontend = self._find_frontend()
        self._check_node_version()
        print(f"Building admin frontend at {frontend}...")
        if self._needs_npm_install(frontend):
            print("Running npm install...")
            run_command(["npm", "install"], cwd=frontend, stream_output=True)
        print("Running npm build")
        run_command(["npm", "run", "build"], cwd=frontend, stream_output=True)
        print("\nAdmin frontend rebuilt successfully.")

    def _find_frontend(self) -> Path:
        from pilot.loader import cli_root

        candidate = cli_root() / "admin" / "frontend"
        if (candidate / "package.json").exists():
            return candidate
        raise BenchError("admin/frontend not found. This command requires the bench-cli source directory with admin/frontend/.")

    def _needs_npm_install(self, frontend: Path) -> bool:
        install_state = frontend / "node_modules" / ".package-lock.json"
        if not install_state.exists():
            return True

        installed_at = install_state.stat().st_mtime
        for manifest in (frontend / "package.json", frontend / "package-lock.json"):
            if manifest.exists() and manifest.stat().st_mtime > installed_at:
                return True
        return False

    def _check_node_version(self) -> None:
        import subprocess

        try:
            output = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise BenchError(
                "Node.js is required to build the admin frontend but was not found. "
                "Install Node.js >= 20.11, or run `bench build-admin` (without --force) to download the pre-built frontend."
            ) from error
        parts = output.lstrip("v").split(".")
        try:
            version = (int(parts[0]), int(parts[1]))
        except (IndexError, ValueError):
            return  # unparseable — let the build run and surface its own error
        if version < _MIN_NODE:
            major, minor = _MIN_NODE
            raise BenchError(
                f"Building the admin frontend requires Node.js >= {major}.{minor}, but found {output}. "
                "Switch to a newer Node (e.g. `nvm use 20`) and retry, or run `bench build-admin` "
                "without --force to download the pre-built frontend instead."
            )
