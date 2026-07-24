from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pilot.exceptions import BenchError

# The frontend toolchain (unplugin via frappe-ui/vite) uses import.meta.dirname,
# which only exists in Node 20.11+. Older Node fails the build with an opaque
# "paths[0] ... undefined" error, so we check up-front.
_MIN_NODE = (20, 11)


def ensure_admin_frontend(on_progress: Callable[[str], None] = lambda message: None) -> None:
    """Build the admin UI from source in dev checkouts; released installs ship it prebuilt.
    Released tarballs carry the source too, so the version - not source presence -
    decides: only dev builds compile, releases always serve the bundled dist."""
    from pilot import is_dev_build
    from pilot.utils import cli_root

    root = cli_root()
    if is_dev_build and _has_frontend_source(root):
        build_admin_frontend(on_progress=on_progress)
        return
    if _has_admin_dist(root):
        return
    raise BenchError(
        "Admin UI is missing from this release. Reinstall bench-cli, or run it from a source checkout."
    )


def build_admin_frontend(on_progress: Callable[[str], None] = lambda message: None) -> None:
    """Compile the admin frontend from source. Requires the admin/frontend/ source and Node.js."""
    from pilot.utils import run_command

    frontend = _find_frontend()
    _check_node_version()
    on_progress(f"Building admin frontend at {frontend}...")
    if _is_npm_install_stale(frontend):
        on_progress("Running npm install...")
        run_command(["npm", "install"], cwd=frontend, stream_output=True)
    on_progress("Running npm run build")
    run_command(["npm", "run", "build"], cwd=frontend, stream_output=True)
    on_progress("\nAdmin frontend built successfully.")


def _has_frontend_source(root: Path) -> bool:
    return (root / "admin" / "frontend" / "package.json").exists()


def _has_admin_dist(root: Path) -> bool:
    return (root / "admin" / "backend" / "static" / "dist" / "assets").exists()


def _find_frontend() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend not found. This command requires the bench-cli source directory with admin/frontend/."
    )


def _is_npm_install_stale(frontend: Path) -> bool:
    install_state = frontend / "node_modules" / ".package-lock.json"
    if not install_state.exists():
        return True

    installed_at = install_state.stat().st_mtime
    for manifest in (frontend / "package.json", frontend / "package-lock.json"):
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
            "Node.js is required to build the admin frontend but was not found. "
            "Install Node.js >= 20.11, or install a released build that ships the prebuilt frontend."
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
