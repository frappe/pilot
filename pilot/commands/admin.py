from __future__ import annotations

import argparse
from pathlib import Path

from pilot.commands.base import Command
from pilot.exceptions import BenchError

_ADMIN_RELEASE_URL = "https://github.com/frappe/bench-cli/releases/download/latest-build/admin-frontend.tar.gz"


def _cli_root() -> Path:
    import pilot as _pkg

    return Path(_pkg.__file__).parent.parent


def download_admin_frontend(cli_root: Path) -> bool:
    """Download and extract the pre-built admin frontend. Returns True on success."""
    import tarfile
    import tempfile
    import urllib.error
    import urllib.request

    static_dir = cli_root / "admin" / "backend" / "static"
    tmp = Path(tempfile.mktemp(suffix=".tar.gz"))

    print(f"Downloading admin frontend from GitHub release...", flush=True)
    try:
        urllib.request.urlretrieve(_ADMIN_RELEASE_URL, tmp)
    except urllib.error.URLError as e:
        print(f"  Download failed: {e}", flush=True)
        tmp.unlink(missing_ok=True)
        return False

    try:
        static_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tmp) as tar:
            tar.extractall(path=static_dir)
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
        from pilot.utils import run_command

        if not self.force_build and download_admin_frontend(_cli_root()):
            return
        if self.force_build:
            print("Skipping download, building from source...")
        else:
            print("Download failed, building from source...")
        frontend = self._find_frontend()
        print(f"Building admin frontend at {frontend}...")
        if not (frontend / "node_modules").exists():
            print("Running npm install...")
            run_command(["npm", "install"], cwd=frontend, stream_output=True)
        print("Running npm build")
        run_command(["npm", "run", "build"], cwd=frontend, stream_output=True)
        print("\nAdmin frontend rebuilt successfully.")

    def _find_frontend(self) -> Path:
        candidate = _cli_root() / "admin" / "frontend"
        if (candidate / "package.json").exists():
            return candidate
        raise BenchError("admin/frontend not found. This command requires the bench-cli source directory with admin/frontend/.")
