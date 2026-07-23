from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pilot
from pilot.exceptions import BenchError
from pilot.utils import cli_root, extract_tar_archive

RELEASE_REPO = "frappe/pilot"
_RELEASES_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases?per_page=1"
_TARBALL_ASSET = "pilot.tar.gz"

Progress = Callable[[str], None]


def latest_release() -> dict | None:
    """Newest release as {tag, asset_url, body}, or None. Uses the releases list (prereleases included)."""
    request = urllib.request.Request(
        _RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "bench-cli"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    if not releases:
        return None
    release = releases[0]
    asset_url = next(
        (a.get("browser_download_url") for a in release.get("assets", []) if a.get("name") == _TARBALL_ASSET),
        None,
    )
    return {"tag": release.get("tag_name"), "asset_url": asset_url, "body": release.get("body", "")}


def update_available() -> tuple[bool, str | None]:
    """Return (is_newer_available, latest_tag) by comparing the newest release tag to __version__."""
    release = latest_release()
    if not release or not release["tag"]:
        return False, None
    return release["tag"] != pilot.__version__, release["tag"]


def perform_upgrade(on_progress: Progress = lambda message: None) -> None:
    """Update the bench-cli code in place. Restarting the admin service is the caller's job."""
    if pilot.is_dev_build:
        _upgrade_dev(on_progress)
    else:
        _upgrade_release(on_progress)


def _upgrade_dev(on_progress: Progress) -> None:
    from admin.backend.frontend import ensure_admin_frontend
    from pilot.managers.environment import AdminEnvManager
    from pilot.utils import run_command

    root = cli_root()
    on_progress("Pulling latest bench-cli (dev install)...")
    run_command(["git", "-C", str(root), "pull"], stream_output=True)
    on_progress("Installing admin Python dependencies...")
    AdminEnvManager(root).install_python_deps()
    on_progress("Rebuilding admin frontend...")
    ensure_admin_frontend(on_progress)


def _upgrade_release(on_progress: Progress) -> None:
    from pilot.managers.environment import AdminEnvManager

    release = latest_release()
    if not release or not release["asset_url"]:
        raise BenchError("No downloadable release asset found; cannot update.")
    if release["tag"] == pilot.__version__:
        on_progress(f"Already on the latest version ({pilot.__version__}).")
        return

    root = cli_root()
    staging = root.with_name(root.name + ".update")
    on_progress(f"Updating {pilot.__version__} -> {release['tag']}...")
    _reset_dir(staging)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tarball = Path(tmp) / _TARBALL_ASSET
            on_progress("Downloading release...")
            urllib.request.urlretrieve(release["asset_url"], tarball)
            on_progress("Extracting new version...")
            extract_tar_archive(tarball, staging)
        on_progress("Swapping in the new version...")
        _swap_in(root, staging, on_progress)
    finally:
        _remove(staging)

    on_progress("Installing admin Python dependencies...")
    AdminEnvManager(root).install_python_deps()
    on_progress(f"Updated to {release['tag']}.")


def _swap_in(root: Path, staging: Path, on_progress: Progress) -> None:
    """Atomically replace each top-level entry the release ships, keeping a rollback backup.

    Directories in the release (pilot/, admin/) are swapped whole, so files dropped between
    versions are pruned. Data dirs (benches/, .admin-venv, .git) are absent from the tarball
    and never touched. A stale top-level entry removed entirely between versions is left in
    place - rare, and harmless. (ponytail: prune those too only if it ever matters.)
    """
    backup = root.with_name(root.name + ".backup")
    _reset_dir(backup)
    swapped: list[tuple[str, bool]] = []
    try:
        for entry in sorted(staging.iterdir()):
            target = root / entry.name
            had_original = target.exists()
            if had_original:
                os.rename(target, backup / entry.name)
            swapped.append((entry.name, had_original))  # record before move-in so rollback covers it
            os.rename(entry, target)
    except Exception:
        on_progress("Update failed; rolling back...")
        for name, had_original in reversed(swapped):
            _remove(root / name)
            if had_original:
                os.rename(backup / name, root / name)
        _remove(backup)  # rollback succeeded; a raise above keeps the backup for recovery
        raise
    _remove(backup)


def _reset_dir(path: Path) -> None:
    _remove(path)
    path.mkdir(parents=True)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
