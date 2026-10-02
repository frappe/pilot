from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from threading import Barrier
from types import SimpleNamespace

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

    def create_venv(self):
        self.bench.python.parent.mkdir(parents=True, exist_ok=True)
        self.bench.python.symlink_to(sys.executable)

    monkeypatch.setattr(PythonEnvManager, "create_venv", create_venv)
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


def test_default_clone_reuses_matching_build_without_sharing_writable_artifacts(source, monkeypatch):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    PythonEnvManager(source).create_venv()
    app = source.apps_path / "frappe"
    dist = app / "frappe/public/dist"
    dist.mkdir(parents=True)
    (dist / "app.js").write_text("built baseline")
    (source.sites_path / "assets/frappe").symlink_to(app / "frappe/public")
    artifacts = BuildArtifacts(source)
    artifacts.capture(artifacts.get_key())
    git(app, "checkout", "-b", "feature")
    (app / "frappe/__init__.py").write_text("feature code")
    builds = []
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: builds.append(self.bench.path))
    destination = source.clone("cached", lambda message: None)
    copied = destination.sites_path / "assets/frappe/dist/app.js"
    assert copied.read_text() == "built baseline"
    assert builds == []
    copied.write_text("destination change")
    assert (dist / "app.js").read_text() == "built baseline"
    assert (app / "frappe/__init__.py").read_text() == "feature code"
    git(app, "checkout", "--", "frappe/__init__.py")
    (app / "frappe/__init__.py").write_text("new remote code")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "update")
    git(app, "push", "origin", "HEAD:main")
    source.clone("changed", lambda message: None)
    assert len(builds) == 1


def test_dependency_copy_requires_matching_install_and_is_independent(tmp_path):
    from pilot.managers.node_dependencies import NodeDependencies

    original, copied = tmp_path / "source", tmp_path / "target"
    for directory in (original, copied):
        directory.mkdir()
        (directory / "package.json").write_text("{}")
        (directory / "yarn.lock").write_text("locked dependencies")
    modules = original / "node_modules"
    modules.mkdir()
    (modules / ".yarn-integrity").write_text("installed")
    (modules / "dependency.js").write_text("original")
    assert not NodeDependencies.copy(original, copied)
    (modules / ".pilot-install-key").write_text(NodeDependencies.get_key(original))
    assert NodeDependencies.copy(original, copied)
    (copied / "node_modules/dependency.js").write_text("target change")
    assert (modules / "dependency.js").read_text() == "original"
    (copied / "yarn.lock").write_text("different dependencies")
    assert not NodeDependencies.copy(original, copied)


def test_build_cache_rejects_changed_build_mode_environment_and_custom_hooks(source, monkeypatch):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    PythonEnvManager(source).create_venv()
    source.write_common_site_config()
    artifacts = BuildArtifacts(source)
    key = artifacts.get_key()
    generated = source.apps_path / "frappe/frappe/public/dist"
    generated.mkdir(parents=True)
    (generated / "bundle.js").write_text("compiled output")
    assert artifacts.get_key() == key
    code = source.apps_path / "frappe/frappe/public/distinct"
    code.mkdir()
    (code / "input.js").write_text("frontend source")
    assert artifacts.get_key() != key
    (code / "input.js").unlink()
    config = source.sites_path / "common_site_config.json"
    original = config.read_text()
    changed = json.loads(original)
    changed["esbuild_target"] = "es2020"
    config.write_text(json.dumps(changed))
    assert artifacts.get_key() != key
    config.write_text(original)
    monkeypatch.setenv("VITE_API_URL", "https://different.example")
    assert artifacts.get_key() != key
    hooks = source.apps_path / "frappe/frappe/hooks.py"
    hooks.write_text("after_build = 'frappe.custom_build'\n")
    assert artifacts.get_key() is None


@pytest.mark.parametrize("failure", [None, "database", "environment", "uploads"])
def test_fork_overlaps_database_and_environment_and_waits_before_cleanup(source, monkeypatch, failure):
    from threading import Event

    started, preparing, completed = Event(), Event(), Event()
    files_completed = Event()
    original = BenchClone.prepare_environment
    copy_files = SiteClone.copy_files
    cleaned = []

    def import_database(self):
        started.set()
        assert preparing.wait(5), "Database import did not overlap environment preparation"
        assert files_completed.wait(5), "Uploads did not overlap database import"
        completed.set()
        if failure == "database":
            raise RuntimeError("database failed")

    def copy_uploads(self, site):
        try:
            assert started.wait(5), "Database import did not overlap upload copying"
            if failure == "uploads":
                raise RuntimeError("uploads failed")
            copy_files(self, site)
        finally:
            files_completed.set()

    def prepare(self, destination, rebuild, on_progress):
        assert started.wait(5), "Database import was not started before environment preparation"
        preparing.set()
        if failure == "environment":
            raise RuntimeError("environment failed")
        original(self, destination, rebuild, on_progress)

    def cleanup(self):
        assert completed.is_set(), "Cleanup raced with the database import"
        assert files_completed.is_set(), "Cleanup raced with upload copying"
        cleaned.append(self.config["db_name"])

    monkeypatch.setattr(BenchClone, "prepare_environment", prepare)
    monkeypatch.setattr(SiteDatabaseClone, "run", import_database)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup)
    monkeypatch.setattr(SiteClone, "finish", lambda *args: None)
    monkeypatch.setattr(SiteClone, "copy_files", copy_uploads)
    monkeypatch.setattr("pilot.core.bench.fork_runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            source.fork("parallel", on_progress=lambda message: None)
        assert len(cleaned) == 1
        assert not (source.path.parent / "parallel/sites/parallel.localhost").exists()
    else:
        destination = source.fork("parallel", on_progress=lambda message: None)
        assert destination.site("parallel.localhost").exists
        assert cleaned == []


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


@pytest.mark.parametrize("stage", ["database", "finish"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_site_clone_releases_route_and_owned_resources(source, monkeypatch, stage, cleanup_fails):
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    original = source.site("source.localhost")
    source_config = (original.path / "site_config.json").read_bytes()
    released, dropped = [], []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr("pilot.core.site.clone.register_with_provider", lambda *args: None)
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))
    monkeypatch.setattr("pilot.core.bench.fork_runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())

    def fail(*args):
        raise RuntimeError("original clone failure")

    def cleanup(self):
        dropped.append(self.config["db_name"])
        if cleanup_fails:
            raise RuntimeError("database cleanup failure")

    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup, raising=False)
    monkeypatch.setattr(SiteDatabaseClone, "run", fail if stage == "database" else lambda self: None)
    monkeypatch.setattr(SiteClone, "finish", fail)
    with pytest.raises(RuntimeError, match="original clone failure"):
        original.clone("failed.localhost", on_progress=lambda message: None)
    assert released == ["failed.localhost"]
    assert len(dropped) == 1 and dropped[0] != "source_db"
    target = source.sites_path / "failed.localhost"
    assert target.exists() == cleanup_fails
    if cleanup_fails:
        assert json.loads((target / "site_config.json").read_text())["db_name"] == dropped[0]
    assert (original.path / "site_config.json").read_bytes() == source_config
    assert (original.path / "public/files/marker.txt").read_text() == "source files"


def test_failed_route_registration_does_not_release_an_unowned_route(source, monkeypatch):
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    released = []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))

    def conflict(*args):
        raise BenchError("route already owned")

    monkeypatch.setattr("pilot.core.site.clone.register_with_provider", conflict)
    with pytest.raises(BenchError, match="route already owned"):
        source.site("source.localhost").clone("conflict.localhost", on_progress=lambda message: None)
    assert released == []
    assert not (source.sites_path / "conflict.localhost").exists()


def test_clone_file_cleanup_failure_keeps_original_error(source, monkeypatch):
    def fail(*args):
        raise RuntimeError("original clone failure")

    def cannot_remove(*args):
        raise OSError("cannot remove recovery files")

    monkeypatch.setattr(SiteDatabaseClone, "run", fail)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: None)
    monkeypatch.setattr("pilot.core.site.clone.shutil.rmtree", cannot_remove)
    with pytest.raises(RuntimeError, match="original clone failure") as caught:
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert any("cannot remove recovery files" in note for note in caught.value.__notes__)
    assert (source.sites_path / "failed.localhost/site_config.json").exists()


@pytest.mark.parametrize("existing_hosts_entry", [False, True])
def test_late_clone_failure_removes_owned_hosts_and_refreshes_nginx(
    source, monkeypatch, existing_hosts_entry
):
    from pathlib import Path

    from pilot.core.site.commands import SiteCommands
    from pilot.core.site.provisioning import SiteProvisioner
    from pilot.managers.nginx import NginxManager

    events = []
    source.config.production.process_manager = "none"
    read_text = Path.read_text

    def hosts(self, *args, **kwargs):
        if str(self) == "/etc/hosts":
            return "127.0.0.1 failed.localhost\n" if existing_hosts_entry else "127.0.0.1 localhost\n"
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", hosts)
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: None)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: events.append("database removed"))
    monkeypatch.setattr(SiteCommands, "set_admin_password", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "write_pilot_communication_config", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "add_to_hosts", lambda *args: events.append("hosts added"))
    monkeypatch.setattr(
        "pilot.managers.platform.remove_hosts_entry", lambda name: events.append("hosts removed")
    )
    monkeypatch.setattr("pilot.core.bench.fork_runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())

    def reload(self):
        if (source.sites_path / "failed.localhost").exists():
            events.append("nginx published")
            raise RuntimeError("nginx publish failed")
        events.append("nginx cleared")

    monkeypatch.setattr(NginxManager, "reload_for_site_change", reload)
    with pytest.raises(RuntimeError, match="nginx publish failed"):
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert events == (
        ["hosts added", "nginx published"]
        + ([] if existing_hosts_entry else ["hosts removed"])
        + ["database removed", "nginx cleared"]
    )


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


@pytest.mark.parametrize("engine", ["mariadb", "postgres"])
def test_clone_database_cleanup_only_drops_generated_destination(source, monkeypatch, engine):
    from pilot.managers.database import MariaDBManager

    statements = []
    monkeypatch.setattr(MariaDBManager, "run_admin_sql", lambda self, sql: statements.append(sql))
    monkeypatch.setattr(SiteDatabaseClone, "postgres_admin_sql", lambda self, sql: statements.append(sql))
    name = "_0123456789abcdef"
    clone = SiteDatabaseClone(
        source.site("source.localhost"), source.site("copy.localhost"), {"db_name": name, "db_type": engine}
    )
    clone.cleanup()
    assert len(statements) == 1
    assert "DROP DATABASE IF EXISTS" in statements[0]
    assert ("DROP ROLE IF EXISTS" if engine == "postgres" else "DROP USER IF EXISTS") in statements[0]
    assert statements[0].count(name) == 2
    clone.config["db_name"] = "source_db"
    with pytest.raises(BenchError, match="not generated"):
        clone.cleanup()
    assert len(statements) == 1


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


@pytest.mark.parametrize("unsupported", [None, "relationships", "view", "engine", "small", "escaped"])
def test_mariadb_clone_parallel_schema_then_single_snapshot_or_full_fallback(
    source, monkeypatch, unsupported
):
    tables = [f"table_{index}" for index in range(16)]
    rows = [
        f"{name}\tBASE TABLE\t{'MyISAM' if index == 0 else 'InnoDB'}" for index, name in enumerate(tables)
    ]
    rows.append("counter\tSEQUENCE\tInnoDB")
    if unsupported == "view":
        rows.append("view\tVIEW\tNULL")
    elif unsupported == "engine":
        rows[0] = "table_0\tBASE TABLE\tMEMORY"
    elif unsupported == "small":
        rows = rows[:15]
    elif unsupported == "escaped":
        rows[0] = "table\\tname\tBASE TABLE\tInnoDB"
    metadata = "\n".join(["1" if unsupported == "relationships" else "0", *rows]).encode()
    monkeypatch.setattr(
        "pilot.core.site.clone_database.run_command", lambda *args, **kwargs: SimpleNamespace(stdout=metadata)
    )
    monkeypatch.setattr("pilot.managers.database.MariaDBManager.run_admin_sql", lambda *args: None)
    streams = []
    schema_ready = Barrier(4)

    def stream(dump, restore, source_env, target_env):
        assert "source-secret" not in dump and "target-secret" not in restore
        assert source_env["MYSQL_PWD"] == "source-secret"
        assert target_env["MYSQL_PWD"] == "target-secret"
        assert "--single-transaction" in dump
        if "--skip-add-drop-table" in dump:
            schema_ready.wait(timeout=5)
        elif unsupported is None and "--no-create-info" in dump:
            assert len(streams) == 4
        elif unsupported is None:
            assert len(streams) == 5 and "--no-create-info" in streams[-1]
        streams.append(dump)

    monkeypatch.setattr("pilot.core.site.clone_database.stream_database", stream)
    clone = SiteDatabaseClone(
        source.site("source.localhost"),
        source.site("copy.localhost"),
        {"db_name": "_0123456789abcdef", "db_password": "target-secret", "db_type": "mariadb"},
    )
    clone.run()
    if unsupported:
        assert len(streams) == 1 and "--no-data" not in streams[0]
        assert streams[0][-1] == "source_db"
    else:
        copied = [table for dump in streams[:4] for table in dump[dump.index("--") + 2 :]]
        assert sorted(copied) == sorted(tables)
        assert all("--no-data" in dump for dump in streams[:4])
        assert len(streams) == 6 and streams[4][-1] == "source_db"
        assert streams[5][-1] == "counter" and "--no-data" in streams[5]
