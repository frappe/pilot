from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pilot
from pilot._vendor.packaging.version import Version
from pilot.config.common import CommonConfig
from pilot.exceptions import BenchError
from pilot.integrations.central import CentralClient
from pilot.integrations.central.client import CentralClientError
from pilot.utils import benches_dir, cli_root, extract_tar_archive

RELEASE_REPO = "frappe/pilot"
_RELEASES_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases?per_page=1"
_RELEASE_BY_TAG_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/tags/{{tag}}"
_TARBALL_ASSET = "pilot.tar.gz"
_OBSOLETE_TOP_LEVEL_ENTRIES = ("bench",)

Progress = Callable[[str], None]


def latest_release() -> dict | None:
    """Newest release as {tag, asset_url, body}, or None. Uses the releases list (prereleases included)."""
    releases = _get_github_json(_RELEASES_API)
    return _release_info(releases[0]) if releases else None


def get_release(tag: str) -> dict:
    """One release by tag, as {tag, asset_url, body}."""
    try:
        return _release_info(_get_github_json(_RELEASE_BY_TAG_API.format(tag=urllib.parse.quote(tag))))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        raise BenchError(f"GitHub has no such release: Pilot {tag}.") from exc


def is_newer(tag: str) -> bool:
    """Whether `tag` is a later release than this install, so an older one is never offered."""
    return Version(tag.split("-")[0]) > Version(pilot.__version__.split("-")[0])


def get_target_release() -> dict | None:
    """The release this host may update to: Central's pick while it runs a rollout, else the newest.

    A Central-managed host that cannot reach Central does not update, so an outage never
    lets every host skip the rollout at once."""
    central = CommonConfig.read(benches_dir()).central
    if not central.enabled:
        return latest_release()
    central.validate()

    rollout = CentralClient().get_pilot_release(central.update_channel)
    if not rollout.get("tag"):
        return latest_release()
    return get_release(rollout["tag"]) if rollout.get("allowed") else None


def _get_github_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pilot"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _release_info(release: dict) -> dict:
    asset_url = next(
        (a.get("browser_download_url") for a in release.get("assets", []) if a.get("name") == _TARBALL_ASSET),
        None,
    )
    return {"tag": release.get("tag_name"), "asset_url": asset_url, "body": release.get("body", "")}


def update_available() -> tuple[bool, str | None]:
    """Return (is_newer_available, latest_tag) by comparing the target release tag to __version__."""
    release = get_target_release()
    if not release or not release["tag"]:
        return False, None
    return is_newer(release["tag"]), release["tag"]


def perform_upgrade(on_progress: Progress = lambda message: None) -> None:
    """Update the Pilot code in place. Restarting the admin service is the caller's job."""
    from pilot.internal.patch_runner import run_patches

    tag = None
    try:
        run_patches("pre_update", on_progress=on_progress)
        if pilot.is_dev_build:
            _upgrade_dev(on_progress)
        else:
            tag = _upgrade_release(on_progress)
        run_patches("post_update", on_progress=on_progress)
    except Exception as exc:
        if not pilot.is_dev_build:
            _report_update(pilot.__version__, f"Pilot update failed: {exc}")
        raise
    if tag:
        _report_update(tag)


def _upgrade_dev(on_progress: Progress) -> None:
    from admin.backend.frontend import ensure_admin_frontend
    from pilot.managers.environment import AdminEnvManager
    from pilot.utils import run_command

    root = cli_root()
    on_progress("Pulling latest Pilot (dev install)...")
    run_command(["git", "-C", str(root), "pull"], stream_output=True)
    on_progress("Installing admin Python dependencies...")
    AdminEnvManager(root).install_python_deps()
    on_progress("Rebuilding admin frontend...")
    ensure_admin_frontend(on_progress)


def _upgrade_release(on_progress: Progress) -> str | None:
    """The tag this host updated to, or None when it stayed put."""
    from pilot.managers.environment import AdminEnvManager

    release = get_target_release()
    if not release:
        on_progress(f"No update available for this server yet ({pilot.__version__}).")
        return None
    if not is_newer(release["tag"]):
        on_progress(f"Already on the latest version ({pilot.__version__}).")
        return None
    if not release["asset_url"]:
        raise BenchError("No downloadable release asset found; cannot update.")

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
    return release["tag"]


def _report_update(version: str, error: str | None = None) -> None:
    """Central counts installs and failures per release; an unreachable Central never fails the update."""
    if not CommonConfig.read(benches_dir()).central.enabled:
        return
    with contextlib.suppress(CentralClientError):
        CentralClient().report_pilot_update(version, error)


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
        for name in _OBSOLETE_TOP_LEVEL_ENTRIES:
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
    _remove(path)
    path.mkdir(parents=True)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
