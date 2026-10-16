from __future__ import annotations

import json
import subprocess
import sys

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.bench import Bench
from pilot.managers.environment import PythonEnvManager

from .git_repository import git


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "benches" / "dev"
    root.mkdir(parents=True)
    config = BenchConfig.default("dev", benches_root=root.parent)
    config.apps = [AppConfig(name="frappe", repo="https://example.com/frappe", branch="main")]
    config.write(root)
    bench = Bench(root)
    bench.create_directories()
    app = bench.apps_path / "frappe"
    package = app / "frappe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'baseline'\n")
    (package / "hooks.py").write_text("")
    git(app, "init", "-b", "main")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline")
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(app), str(remote)], check=True, capture_output=True)
    git(app, "remote", "add", "origin", str(remote))
    bench.write_apps_txt()
    site = bench.site("source.localhost")
    site.path.mkdir()
    (site.path / "site_config.json").write_text(
        json.dumps(
            {
                "db_name": "source_db",
                "db_password": "source-secret",
                "encryption_key": "source-key",
                "pilot_auth_token": "source-token",
                "domains": ["source.example.com"],
                "redis_cache": "redis://source:1234",
            }
        )
    )
    for relative in ("public/files", "private/files"):
        files = site.path / relative
        files.mkdir(parents=True)
        (files / "marker.txt").write_text("source files")

    def create_venv(self):
        self.bench.python.parent.mkdir(parents=True, exist_ok=True)
        self.bench.python.symlink_to(sys.executable)

    monkeypatch.setattr(PythonEnvManager, "create_venv", create_venv)
    monkeypatch.setattr(PythonEnvManager, "install_app", lambda self, app: None)
    monkeypatch.setattr(PythonEnvManager, "install_apps", lambda self, apps: None)
    monkeypatch.setattr(PythonEnvManager, "install_node_dependencies", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: None)
    monkeypatch.setattr("pilot.core.site.clone.query_installed_apps_via_db", lambda *args: ["frappe"])
    return bench
