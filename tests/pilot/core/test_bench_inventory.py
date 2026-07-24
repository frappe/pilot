"""Tests for BenchInventory.app()/apps() branch resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from pilot.config import AppConfig
from pilot.core.bench.inventory import BenchInventory


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(path)], check=True)
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    (path / "file").write_text("init")
    _git(path, "add", "file")
    _git(path, "commit", "-q", "-m", "init")


def test_app_falls_back_to_configured_branch_when_detached(tmp_path: Path) -> None:
    """A revert leaves the repo in detached HEAD; the configured branch should still resolve."""
    apps_path = tmp_path / "apps"
    app_path = apps_path / "frappe"
    _init_repo(app_path)
    _git(app_path, "checkout", "--detach", "HEAD")

    mock_bench = MagicMock()
    mock_bench.apps_path = apps_path
    mock_bench.config.apps = [AppConfig(name="frappe", repo="", branch="develop")]

    app = BenchInventory(mock_bench).app("frappe")

    assert app.config.branch == "develop"


def test_app_prefers_live_branch_over_configured_branch(tmp_path: Path) -> None:
    apps_path = tmp_path / "apps"
    app_path = apps_path / "frappe"
    _init_repo(app_path)

    mock_bench = MagicMock()
    mock_bench.apps_path = apps_path
    mock_bench.config.apps = [AppConfig(name="frappe", repo="", branch="version-15")]

    app = BenchInventory(mock_bench).app("frappe")

    assert app.config.branch == "develop"
