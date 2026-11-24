from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pilot
from pilot.exceptions import BenchError
from pilot.internal.archive import TarArchive
from pilot.internal.git import GitRepo

RELEASE_REPO = "frappe/pilot"
_TARBALL_ASSET = "pilot.tar.gz"

Progress = Callable[[str], None]


def latest_release() -> dict | None:
    """Newest release as {tag, asset_url}, or None. Reads the public releases feed, which lists
    prereleases and is not under the GitHub API rate limit."""
    request = urllib.request.Request(
        f"https://github.com/{RELEASE_REPO}/releases.atom", headers={"User-Agent": "pilot"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        feed = ElementTree.parse(response).getroot()
    link = feed.find("atom:entry/atom:link[@rel='alternate']", {"atom": "http://www.w3.org/2005/Atom"})
    if link is None:
        return None
    tag = link.get("href", "").rsplit("/", 1)[-1]
    return {
        "tag": tag,
        "asset_url": f"https://github.com/{RELEASE_REPO}/releases/download/{tag}/{_TARBALL_ASSET}",
    }


def update_available() -> tuple[bool, str | None]:
    """Return (is_newer_available, latest_tag) by comparing the newest release tag to __version__."""
    release = latest_release()
    if not release or not release["tag"]:
        return False, None
    return release["tag"] != pilot.__version__, release["tag"]


def get_installed_version() -> str:
    """The Pilot version on disk: the release tag, or `dev` and the commit of a dev checkout."""
    from pilot.core.server import Server

    root = Server().root
    if (version_file := root / "VERSION").exists():
        return version_file.read_text().strip()
    return f"dev {GitRepo(root).short_head}".strip()


def perform_upgrade(on_progress: Progress = lambda message: None) -> None:
    """Update the Pilot code in place and restart the monitor, and the database agent of a database
    server. Restarting the admin service is the caller's job."""
    from pilot.core.server import Server
    from pilot.core.server.monitoring_config import MonitorConfigurator
    from pilot.internal.patch_runner import run_patches

    is_database_server = Server().is_database_server
    run_patches("pre_update", on_progress=on_progress)
    if pilot.is_dev_build:
        _upgrade_dev(on_progress, has_admin=not is_database_server)
    else:
        _upgrade_release(on_progress)
    run_patches("post_update", on_progress=on_progress)
    # The monitor runs for the life of the host, so it keeps the old modules until restarted.
    MonitorConfigurator().restart()
    if is_database_server:
        on_progress("Restarting the database agent...")
        restart_database_agent_with_new_code()


def restart_admin_with_new_code(bench_path: Path) -> None:
    """Restart the admin service from a new process, because this process still holds the old modules."""
    from pilot.core.server import Server

    env = {**os.environ, "PYTHONPATH": str(Server().root)}
    # -P: PYTHONPATH finds Pilot, not the caller's working directory, which may be a bench's sites/.
    subprocess.run([sys.executable, "-P", "-m", "pilot.updater", str(bench_path)], check=True, env=env)


def restart_database_agent_with_new_code() -> None:
    """Rewrite and restart the database agent's unit from a new process, which holds the new code.
    MariaDB keeps running."""
    from pilot.core.server import Server

    env = {**os.environ, "PYTHONPATH": str(Server().root)}
    subprocess.run([sys.executable, "-P", "-m", "pilot.core.database.agent.setup"], check=True, env=env)


def restart_admin(bench_path: Path) -> None:
    """Restart a managed admin service. `pilot start` must be restarted by the operator."""
    from pilot.core.bench import Bench
    from pilot.managers.processes.base import ManagedProcessManager
    from pilot.managers.processes.local import ProcessManager

    manager = ProcessManager.detect_running(Bench(bench_path))
    if not isinstance(manager, ManagedProcessManager):
        print("`pilot start` runs the admin here. Restart it to run the new version.")
        return
    print("Restarting admin service...")
    manager.restart_admin()


def _upgrade_dev(on_progress: Progress, has_admin: bool) -> None:
    """Upgrade a dev checkout. A host without an Admin, such as a database server, skips its frontend."""
    from admin.backend.frontend import ensure_admin_frontend
    from pilot.core.server import Server
    from pilot.internal.command import run_command
    from pilot.managers.environment import AdminEnvManager

    root = Server().root
    on_progress("Pulling latest Pilot (dev install)...")
    run_command(["git", "-C", str(root), "pull"], stream_output=True)
    on_progress("Installing admin Python dependencies...")
    AdminEnvManager(root).install_python_deps()
    if has_admin:
        on_progress("Rebuilding admin frontend...")
        ensure_admin_frontend(on_progress)


def _upgrade_release(on_progress: Progress) -> None:
    """Upgrade to the latest release."""
    from pilot.core.server import Server
    from pilot.managers.environment import AdminEnvManager

    release = latest_release()
    if not release or not release["asset_url"]:
        raise BenchError("No downloadable release asset found; cannot update.")
    if release["tag"] == pilot.__version__:
        on_progress(f"Already on the latest version ({pilot.__version__}).")
        return

    root = Server().root
    staging = root.with_name(root.name + ".update")
    on_progress(f"Updating {pilot.__version__} -> {release['tag']}...")
    _reset_dir(staging)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tarball = Path(tmp) / _TARBALL_ASSET
            on_progress("Downloading release...")
            urllib.request.urlretrieve(release["asset_url"], tarball)
            on_progress("Extracting new version...")
            TarArchive(tarball).extract_to(staging)
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
    and never touched. Top-level entries are otherwise preserved unless explicitly listed
    as obsolete.
    """
    backup = root.with_name(root.name + ".backup")
    _reset_dir(backup)
    swapped: list[tuple[str, bool]] = []
    try:
        for name in ("bench",):
            target = root / name
            if target.exists() or target.is_symlink():
                os.rename(target, backup / name)
                swapped.append((name, True))
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
    """Remove the path and create an empty directory."""
    _remove(path)
    path.mkdir(parents=True)


def _remove(path: Path) -> None:
    """Remove a file, link or directory."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


# An older Pilot runs this module after it updates, so keep this command line stable.
if __name__ == "__main__":
    restart_admin(Path(sys.argv[1]))
