from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pilot.exceptions import BenchError

_ADMIN_RELEASE_URL = (
    "https://github.com/frappe/bench-cli/releases/download/latest-build/admin-frontend.tar.gz"
)
# The frontend toolchain (unplugin via frappe-ui/vite) uses import.meta.dirname,
# which only exists in Node 20.11+. Older Node fails the build with an opaque
# "paths[0] ... undefined" error, so we check up-front.
_MIN_NODE = (20, 11)


def download_admin_frontend(cli_root: Path) -> bool:
    """Download and extract the pre-built admin frontend. Returns True on success."""
    import tempfile
    import urllib.error
    import urllib.request

    from pilot.utils import extract_tar_archive

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


def build_admin_frontend(on_progress: Callable[[str], None] = lambda message: None) -> None:
    """Build the admin frontend and editor from source. Requires Node.js."""
    _check_node_version()
    _build_frontend(_find_frontend(), "dashboard", on_progress)
    _build_frontend(_find_editor(), "editor", on_progress)
    on_progress("\nAdmin frontend rebuilt successfully.")


def _build_frontend(frontend: Path, label: str, on_progress: Callable[[str], None]) -> None:
    from pilot.utils import run_command

    on_progress(f"Building {label} frontend at {frontend}...")
    if _is_yarn_install_stale(frontend):
        on_progress("Running yarn install...")
        run_command(["yarn", "install"], cwd=frontend, stream_output=True)
    on_progress("Running yarn build")
    run_command(["yarn", "build"], cwd=frontend, stream_output=True)


def _find_frontend() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend" / "dashboard"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend/dashboard not found. This command requires the bench-cli source directory "
        "with admin/frontend/dashboard/."
    )


def _find_editor() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend" / "editor"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend/editor not found. This command requires the bench-cli source directory "
        "with admin/frontend/editor/."
    )


def _is_yarn_install_stale(frontend: Path) -> bool:
    install_state = frontend / "node_modules" / ".yarn-integrity"
    if not install_state.exists():
        return True

    installed_at = install_state.stat().st_mtime
    for manifest in (frontend / "package.json", frontend / "yarn.lock"):
        if manifest.exists() and manifest.stat().st_mtime > installed_at:
            return True
    return False


def _check_node_version() -> None:
    import subprocess

    try:
        output = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise BenchError(
            "Node.js is required to build the admin frontend but was not found. Install Node.js >= 20.11."
        ) from error
    parts = output.lstrip("v").split(".")
    try:
        version = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return  # unparseable - let the build run and surface its own error
    if version < _MIN_NODE:
        major, minor = _MIN_NODE
        raise BenchError(
            f"Building the admin frontend requires Node.js >= {major}.{minor}, but found {output}. "
            "Switch to a newer Node (e.g. `nvm use 20`) and retry."
        )
