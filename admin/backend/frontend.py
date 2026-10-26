from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

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
    if is_admin_frontend_built(root):
        return
    raise BenchError(
        "Admin UI is missing from this release. Reinstall Pilot, or run it from a source checkout."
    )


def build_admin_frontend(
    on_progress: Callable[[str], None] = lambda message: None, *, force: bool = False
) -> None:
    """Build dashboard, editor, and in-app-embed frontends from source, skipping any
    whose dist already exists unless force is set."""
    from pilot.utils import cli_root

    _check_node_version()
    root = cli_root()
    _build_frontend(
        _find_frontend(), root / "admin" / "backend" / "static" / "dashboard", "dashboard", on_progress, force=force
    )
    _build_frontend(
        _find_editor(), root / "admin" / "backend" / "static" / "editor", "editor", on_progress, force=force
    )
    _build_frontend(
        _find_in_app_embed(),
        root / "admin" / "backend" / "static" / "in-app-embed" / "cloud-settings",
        "in-app-embed",
        on_progress,
        force=force,
    )
    on_progress("\nAdmin frontend up to date.")


def _build_frontend(
    frontend: Path, dist: Path, label: str, on_progress: Callable[[str], None], *, force: bool = False
) -> None:
    from pilot.utils import run_command

    if not force and _is_built(dist):
        on_progress(f"{label} frontend is already built, skipping.")
        return
    on_progress(f"Building {label} frontend at {frontend}...")
    if _is_npm_install_stale(frontend):
        on_progress(f"Running npm install for {label}...")
        run_command(["npm", "install"], cwd=frontend, stream_output=True)
    on_progress(f"Running npm run build for {label}")
    dist.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{label}-build-", dir=dist.parent) as temporary:
        staged = Path(temporary)
        run_command(["npm", "run", "build", "--", "--outDir", str(staged)], cwd=frontend, stream_output=True)
        if not _is_built(staged):
            raise BenchError(f"The {label} build did not produce a complete frontend.")
        files = sorted(staged.rglob("*"), key=lambda path: path.name == "index.html")
        for source in files:
            if source.is_file():
                target = dist / source.relative_to(staged)
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)


def _is_built(dist: Path) -> bool:
    if not dist.is_dir():
        return False
    # Dashboard/editor write an assets/ folder; the in-app embed ships a single IIFE.
    return ((dist / "index.html").is_file() and (dist / "assets").is_dir()) or any(dist.glob("*.js"))


def _has_frontend_source(root: Path) -> bool:
    return (root / "admin" / "frontend" / "dashboard" / "package.json").exists()


def is_admin_frontend_built(root: Path) -> bool:
    return _is_built(root / "admin" / "backend" / "static" / "dashboard")


def _find_frontend() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend" / "dashboard"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend/dashboard not found. This command requires the Pilot source directory "
        "with admin/frontend/dashboard/."
    )


def _find_editor() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend" / "editor"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend/editor not found. This command requires the Pilot source directory "
        "with admin/frontend/editor/."
    )


def _find_in_app_embed() -> Path:
    from pilot.utils import cli_root

    candidate = cli_root() / "admin" / "frontend" / "in-app-embed"
    if (candidate / "package.json").exists():
        return candidate
    raise BenchError(
        "admin/frontend/in-app-embed not found. This command requires the Pilot source directory "
        "with admin/frontend/in-app-embed/."
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
    from pilot.exceptions import CommandError
    from pilot.utils import run_command

    try:
        output = run_command(["node", "--version"], timeout=5).stdout.decode().strip()
    except (OSError, CommandError) as error:
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
