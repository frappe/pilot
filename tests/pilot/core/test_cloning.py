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
    monkeypatch.setattr(PythonEnvManager, "create_venv", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "install_app", lambda self, app: None)
    monkeypatch.setattr(PythonEnvManager, "install_node_dependencies", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: None)
    monkeypatch.setattr("pilot.core.site.clone.query_installed_apps_via_db", lambda *args: ["frappe"])
    return bench


def test_bench_clone_preserves_dirty_files_but_has_independent_git_and_ports(source):
    app = source.apps_path / "frappe"
    (app / "frappe/__init__.py").write_text("VALUE = 'dirty'\n")
    (app / "untracked.txt").write_text("local change")
    (source.sites_path / "assets/frappe").symlink_to(app / "frappe")
    destination = source.clone("uat", lambda message: None, branch="current")
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


def test_default_clone_uses_live_origin_default_and_excludes_feature_changes(source, monkeypatch):
    default_branch = "trunk"
    app = source.apps_path / "frappe"
    remote = git(app, "remote", "get-url", "origin")
    if default_branch != "main":
        git(app, "branch", "-m", default_branch)
    git(app, "push", "origin", default_branch)
    subprocess.run(
        ["git", "--git-dir", remote, "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"], check=True
    )
    upstream = source.path.parent / "upstream"
    subprocess.run(["git", "clone", remote, str(upstream)], check=True, capture_output=True)
    (upstream / "upstream-only.txt").write_text("latest remote commit")
    git(upstream, "add", ".")
    git(
        upstream,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "remote advance",
    )
    git(upstream, "push", "origin", default_branch)
    git(app, "checkout", "-b", "feature")
    (app / "feature.txt").write_text("feature commit")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feature")
    (app / "frappe/__init__.py").write_text("dirty feature")
    (app / "untracked.txt").write_text("untracked")
    before = git(app, "status", "--porcelain")
    builds = []
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: builds.append(self.bench.path))
    destination = source.clone("default-copy", lambda message: None)
    copied = destination.apps_path / "frappe"
    assert git(copied, "branch", "--show-current") == default_branch
    assert git(copied, "status", "--porcelain") == ""
    assert (copied / "frappe/__init__.py").read_text() == "VALUE = 'baseline'\n"
    assert not (copied / "feature.txt").exists()
    assert not (copied / "untracked.txt").exists()
    assert (copied / "upstream-only.txt").read_text() == "latest remote commit"
    assert not (app / "upstream-only.txt").exists()
    assert git(app, "status", "--porcelain") == before
    assert git(app, "branch", "--show-current") == "feature"
    assert builds == [destination.path]
    assert destination.config.apps[0].branch == default_branch


def test_per_app_branch_override_is_fetched_and_checked_out_cleanly(source):
    app = source.apps_path / "frappe"
    git(app, "checkout", "-b", "feature")
    (app / "feature.txt").write_text("feature")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feature")
    git(app, "push", "origin", "feature")
    (app / "feature.txt").write_text("dirty")
    destination = source.clone("override", lambda message: None, app_branches={"frappe": "feature"})
    assert (destination.apps_path / "frappe/feature.txt").read_text() == "feature"
    assert destination.config.apps[0].branch == "feature"


def test_default_requires_origin_and_unknown_overrides_fail(source):
    git(source.apps_path / "frappe", "remote", "remove", "origin")
    with pytest.raises(BenchError, match="no origin"):
        source.clone("no-origin", lambda message: None)
    with pytest.raises(BenchError, match="Unknown app"):
        source.clone("unknown", app_branches={"missing": "main"})


def test_clone_branch_options_list_origin_branches_with_default_first(source):
    app = source.apps_path / "frappe"
    git(app, "branch", "feature/test")
    git(app, "push", "origin", "feature/test")
    git(app, "branch", "local-only")
    before = git(app, "status", "--porcelain")
    assert source.get_clone_branch_options() == [
        {"name": "frappe", "default_branch": "main", "branches": ["main", "feature/test"]}
    ]
    assert git(app, "status", "--porcelain") == before


def test_clone_branch_options_fail_when_origin_is_unavailable(source):
    git(source.apps_path / "frappe", "remote", "set-url", "origin", "/missing-pilot-test-origin")
    with pytest.raises(BenchError, match="Could not read branches for frappe"):
        source.get_clone_branch_options()


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


def test_current_clone_does_not_inherit_source_worktrees(source):
    from pilot.internal.git import GitRepo

    app = source.apps_path / "frappe"
    worktree = source.path.parent / "task"
    git(app, "worktree", "add", "-b", "task", str(worktree))
    destination = source.clone("independent", lambda message: None, branch="current")
    copied = destination.apps_path / "frappe"
    assert str(worktree) not in git(copied, "worktree", "list", "--porcelain")
    GitRepo(copied).ensure_removable()
    assert str(worktree) in git(app, "worktree", "list", "--porcelain")
    assert git(worktree, "rev-parse", "--git-common-dir") == str(app / ".git")


def test_branch_discovery_and_fetch_use_source_credentials(source, monkeypatch):
    from pilot.core.bench import clone_branches
    from pilot.integrations.git import auth_config_for
    from pilot.integrations.git.credentials import GitCredentialStore
    from pilot.internal.git import git_env

    app = source.apps_path / "frappe"
    local_origin = git(app, "remote", "get-url", "origin")
    source.config.apps[0].repo = "https://github.com/example/private-app.git"
    git(app, "remote", "set-url", "origin", source.config.apps[0].repo)
    source.config.write(source.path)
    GitCredentialStore(source.path).save("github", "test-private-token")
    expected = git_env(auth_config_for(source.path, source.config.apps[0].repo))
    original = clone_branches.run_command
    authenticated = []

    def run(argv, **kwargs):
        if "ls-remote" in argv or "fetch" in argv:
            authenticated.append(argv)
            for key, value in expected.items():
                if key.startswith("GIT_CONFIG_"):
                    assert kwargs["env"][key] == value
            argv = [local_origin if value == "origin" else value for value in argv]
        return original(argv, **kwargs)

    monkeypatch.setattr(clone_branches, "run_command", run)
    assert source.get_clone_branch_options()[0]["default_branch"] == "main"
    destination = source.clone("authenticated", lambda message: None)
    assert sum("ls-remote" in argv for argv in authenticated) == 2
    assert sum("fetch" in argv for argv in authenticated) == 1
    assert "test-private-token" not in (destination.apps_path / "frappe/.git/config").read_text()


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"db_host": "remote.example", "db_port": 3307}, ["--host", "remote.example", "--port", "3307"]),
        ({"db_socket": "/tmp/site.sock", "db_host": "remote.example"}, ["--socket", "/tmp/site.sock"]),
        ({}, ["--socket", "/tmp/bench.sock"]),
    ],
)
def test_database_clone_respects_site_connection_settings(source, config, expected):
    source.config.mariadb.socket_path = "/tmp/bench.sock"
    assert SiteDatabaseClone.mysql_args({"db_name": "site_db", **config}, source) == [
        "--user",
        "site_db",
        *expected,
    ]


def test_postgres_clone_uses_resolved_client_binaries(source, monkeypatch):
    source.config.db_type = "postgres"
    original = source.site("source.localhost")
    destination = source.site("uat.localhost")
    config = {"db_name": "_target", "db_password": "target-secret"}
    sql_calls = []
    streams = []
    monkeypatch.setattr(
        "pilot.managers.database.PostgresManager.client_binary", lambda self, name: f"/brew/bin/{name}"
    )
    monkeypatch.setattr(
        "pilot.core.site.clone_database.subprocess.run", lambda argv, **kwargs: sql_calls.append(argv)
    )
    monkeypatch.setattr("pilot.core.site.clone_database.stream_database", lambda *args: streams.append(args))
    SiteDatabaseClone(original, destination, config).postgres(
        {"db_name": "source_db", "db_password": "source-secret"}
    )
    assert sql_calls[0][0] == "/brew/bin/psql"
    assert streams[0][0][0] == "/brew/bin/pg_dump"
    assert streams[0][1][0] == "/brew/bin/psql"
