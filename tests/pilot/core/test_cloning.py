from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import nullcontext

import pytest

from admin.backend.internal.session import Session
from pilot.config import AppConfig, BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.clone import BenchClone
from pilot.core.site.clone import SiteClone
from pilot.core.site.clone_database import SiteDatabaseClone, stream_database
from pilot.exceptions import BenchError
from pilot.managers.environment import PythonEnvManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


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
    monkeypatch.setattr(PythonEnvManager, "create_venv", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "install_app", lambda self, app: None)
    monkeypatch.setattr("pilot.core.site.clone.query_installed_apps_via_db", lambda *args: ["frappe"])
    return bench


def test_bench_clone_preserves_dirty_files_but_has_independent_git_and_ports(source):
    app = source.apps_path / "frappe"
    (app / "frappe/__init__.py").write_text("VALUE = 'dirty'\n")
    (app / "untracked.txt").write_text("local change")
    (source.sites_path / "assets/frappe").symlink_to(app / "frappe")
    destination = source.clone("uat", lambda message: None)
    copied = destination.apps_path / "frappe"
    assert destination.sites() == []
    assert destination.config.http_port != source.config.http_port
    assert Session(destination).ensure_jwt_secret() != Session(source).ensure_jwt_secret()
    assert git(copied, "status", "--porcelain") == git(app, "status", "--porcelain")
    assert (destination.sites_path / "assets/frappe").resolve() == copied / "frappe"
    (copied / "frappe/__init__.py").write_text("destination change")
    assert "dirty" in (app / "frappe/__init__.py").read_text()
    git(copied, "branch", "destination-only")
    assert "destination-only" not in git(app, "branch")
    assert (copied / ".git/HEAD").stat().st_ino != (app / ".git/HEAD").stat().st_ino


@pytest.mark.parametrize("staged", [False, True])
def test_copying_linked_worktree_materializes_repository(tmp_path, staged):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "marker").write_text("baseline")
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline")
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-b", "task", str(worktree))
    (worktree / "marker").write_text("dirty")
    if staged:
        git(worktree, "add", "marker")
    destination = tmp_path / "copy"
    BenchClone.copy_app(worktree, destination)
    assert (destination / ".git").is_dir()
    assert git(destination, "status", "--porcelain") == git(worktree, "status", "--porcelain")
    git(repo, "worktree", "remove", "--force", str(worktree))
    assert git(destination, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert (destination / "marker").read_text() == "dirty"


def test_site_clones_are_fresh_and_files_and_credentials_are_independent(source, monkeypatch):
    imports = []
    original = source.site("source.localhost")
    (original.path / "public/files/alias.txt").symlink_to(original.path / "public/files/marker.txt")
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: imports.append(self.config.copy()))
    monkeypatch.setattr(SiteClone, "finish", lambda self, site: None)
    monkeypatch.setattr("pilot.core.bench.fork_runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())
    first = source.site("source.localhost").clone("first.localhost", on_progress=lambda message: None)
    original = source.site("source.localhost")
    (original.path / "public/files/marker.txt").write_text("new source data")
    second = original.clone("second.localhost", on_progress=lambda message: None)
    assert len(imports) == 2
    assert (first.path / "logs").is_dir()
    assert (first.path / "locks").is_dir()
    assert imports[0]["db_name"] != imports[1]["db_name"] != "source_db"
    assert imports[0]["db_password"] != imports[1]["db_password"] != "source-secret"
    assert imports[0]["encryption_key"] == "source-key"
    assert imports[0]["pause_scheduler"] == imports[0]["mute_emails"] == 1
    assert not {"domains", "redis_cache", "pilot_auth_token"} & imports[0].keys()
    assert (first.path / "public/files/marker.txt").read_text() == "source files"
    assert (first.path / "public/files/alias.txt").resolve() == first.path / "public/files/marker.txt"
    assert (second.path / "public/files/marker.txt").read_text() == "new source data"
    (first.path / "private/files/marker.txt").write_text("changed destination")
    assert (original.path / "private/files/marker.txt").read_text() == "source files"


def test_clone_rejects_existing_destinations_and_missing_apps(source):
    with pytest.raises(BenchError, match="already exists"):
        source.clone("dev")
    with pytest.raises(BenchError, match="already exists"):
        SiteClone(source.site("source.localhost"), source, "source.localhost", "admin").validate()
    destination = source.clone("empty", lambda message: None)
    (destination.sites_path / "apps.txt").write_text("")
    with pytest.raises(BenchError, match="not installed"):
        SiteClone(source.site("source.localhost"), destination, "uat.localhost", "admin").validate()


@pytest.mark.parametrize("dump_exit,import_exit", [(1, 0), (0, 1), (1, 1)])
def test_stream_reports_failure_of_either_process(dump_exit, import_exit):
    dump = [sys.executable, "-c", f"import sys; print('sql'); sys.exit({dump_exit})"]
    restore = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.exit({import_exit})"]
    with pytest.raises(BenchError, match="Database clone failed"):
        stream_database(dump, restore, os.environ.copy(), os.environ.copy())


def test_stream_transfers_large_input_without_a_dump_file(tmp_path):
    target = tmp_path / "received"
    dump = [sys.executable, "-c", "import sys; sys.stdout.write('sql' * 1_000_000)"]
    restore = [
        sys.executable,
        "-c",
        "import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
        str(target),
    ]
    stream_database(dump, restore, os.environ.copy(), os.environ.copy())
    assert target.stat().st_size == 3_000_000
    assert list(tmp_path.iterdir()) == [target]
