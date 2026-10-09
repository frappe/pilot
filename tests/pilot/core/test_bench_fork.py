from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import Event

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.bench import Bench
from pilot.core.bench.artifacts import BenchArtifacts
from pilot.core.site.commands import SiteCommands
from pilot.core.site.template import SiteTemplate
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo
from pilot.managers.environment import PythonEnvManager


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "benches" / "dev"
    root.mkdir(parents=True)
    config = BenchConfig.default("dev", benches_root=root.parent)
    config.apps = [
        AppConfig(name=name, repo=f"https://example.com/{name}", branch="main")
        for name in ("frappe", "custom")
    ]
    config.mariadb.existing = True
    config.mariadb.port = 3337
    config.write(root)
    bench = Bench(root)
    bench.create_directories()
    entries = []
    for app in bench.init_apps():
        package = app.path / app.config.name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text('VALUE = "baseline"\n')
        (package / "hooks.py").write_text("")
        (app.path / "pyproject.toml").write_text(f'[project]\nname = "{app.config.name}"\nversion = "1.0"\n')
        (app.path / ".gitignore").write_text("node_modules/\n__pycache__/\n")
        git(app.path, "init", "-b", "main")
        git(app.path, "add", ".")
        git(app.path, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "baseline")
        entries.append(
            {"name": app.config.name, "repo": app.config.repo, "commit": GitRepo(app.path).head_sha}
        )
    bench.write_apps_txt()
    template = tmp_path / "template"
    template.mkdir()
    files = {
        "database": "db.sql.gz",
        "public": "files.tar",
        "private": "private.tar",
        "config": "config.json",
    }
    for name in files.values():
        (template / name).write_text("fixture")
    (template / files["config"]).write_text(
        json.dumps(
            {
                "db_name": "source_db",
                "db_password": "source-secret",
                "encryption_key": "fixture-key",
                "pilot_auth_token": "source-token",
                "domains": ["production.example.com"],
                "redis_cache": "redis://source:1234",
                "maintenance_mode": 1,
            }
        )
    )
    (template / "template.json").write_text(
        json.dumps(
            {
                "version": 1,
                "engine": "mariadb",
                "apps": entries,
                "files": files,
            }
        )
    )
    return bench, template


@pytest.fixture
def fake_services(monkeypatch):
    calls = []
    monkeypatch.setattr(PythonEnvManager, "create_venv", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "install_app", lambda self, app: None)
    monkeypatch.setattr(PythonEnvManager, "install_apps", lambda self, apps: None)
    monkeypatch.setattr(PythonEnvManager, "install_node_dependencies", lambda self: None)
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: None)
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.ForkRuntime", lambda bench: nullcontext())
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")

    @contextmanager
    def credentials(self, engine, database=""):
        calls.append((engine, database))
        yield ["--db-root-username", "scoped-test-user"]

    monkeypatch.setattr(SiteCommands, "setup_credentials", credentials)
    monkeypatch.setattr(
        SiteCommands,
        "set_admin_password",
        lambda self, password: calls.append(("password", self.site.config.name, password)),
    )
    monkeypatch.setattr("pilot.core.site.template.run_command", lambda argv, **kwargs: calls.append(argv))
    return calls


def test_parallel_forks_isolate_framework_apps_database_and_ports(prepared, fake_services):
    source, template = prepared
    source_toml = (source.path / "bench.toml").read_bytes()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(source.fork, name, template, lambda message: None)
            for name in ("task-a", "task-b")
        ]
        first, second = [future.result() for future in futures]
    ports = []
    databases = []
    for bench in (source, first, second):
        config = bench.config
        current = {
            config.http_port,
            config.socketio_port,
            config.redis.cache_port,
            config.redis.queue_port,
            config.admin.port,
            config.admin.port + 1,
        }
        assert not any(current & previous for previous in ports)
        ports.append(current)
    for bench in (first, second):
        assert not bench.config.production.enabled
        assert bench.config.mariadb.port == 3337
        site_config = json.loads(
            (bench.site(f"{bench.config.name}.localhost").path / "site_config.json").read_text()
        )
        databases.append(site_config["db_name"])
        assert site_config["db_password"] != "source-secret"
        assert site_config["encryption_key"] == "fixture-key"
        assert site_config["pilot_auth_token"] != "source-token"
        assert site_config["pause_scheduler"] == 1
        assert site_config["maintenance_mode"] == 0
        assert site_config["db_port"] == 3337
        assert "domains" not in site_config
        assert "redis_cache" not in site_config
        assert json.loads((bench.path / "fork.json").read_text())["status"] == "ready"
        for name in ("frappe", "custom"):
            assert GitRepo(bench.app(name).path).is_worktree
            (bench.app(name).path / name / "__init__.py").write_text(bench.config.name)
    assert len(set(databases)) == 2
    for app in source.apps():
        assert '"baseline"' in (app.path / app.config.name / "__init__.py").read_text()
    assert (source.path / "bench.toml").read_bytes() == source_toml
    restores = [call for call in fake_services if isinstance(call, list) and "restore" in call]
    assert len(restores) == 2
    assert all("restore" in call and "new-site" not in call for call in restores)
    passwords = [call[2] for call in fake_services if call[0] == "password"]
    assert len(set(passwords)) == 2 and "admin" not in passwords
    assert all("--admin-password" not in call for call in restores)


def test_failed_fork_keeps_owned_state_without_touching_source(prepared, fake_services, monkeypatch):
    source, template = prepared

    def fail(self, app):
        raise RuntimeError("dependency conflict")

    monkeypatch.setattr(PythonEnvManager, "install_app", fail)
    with pytest.raises(RuntimeError, match="dependency conflict"):
        source.fork("failed", template, lambda message: None)
    destination = source.path.parent / "failed"
    state = json.loads((destination / "fork.json").read_text())
    assert state["status"] == "failed"
    assert "python" in state["timings"]
    assert GitRepo(destination / "apps" / "frappe").is_worktree
    assert GitRepo(source.app("frappe").path).branch == "main"


@pytest.mark.parametrize("target", ["../outside", "dev", "pilot"])
def test_invalid_or_existing_target_does_not_create_worktrees(prepared, target):
    source, template = prepared
    with pytest.raises(BenchError):
        source.fork(target, template)
    assert git(source.app("frappe").path, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_missing_template_file_fails_before_destination_creation(prepared):
    source, template = prepared
    (template / "db.sql.gz").unlink()
    with pytest.raises(BenchError, match="template file"):
        source.fork("missing", template)
    assert not (source.path.parent / "missing").exists()


def test_worktree_staging_move_cleanup_and_source_guard(prepared):
    source, _ = prepared
    app = source.app("custom")
    checkout = source.path.parent / "agent" / "apps" / "custom"
    app.create_worktree(checkout, "HEAD", "agent/test")
    with pytest.raises(BenchError, match="owns other worktrees"):
        GitRepo(app.path).ensure_removable()
    destination = Bench(BenchConfig.default("agent"), checkout.parent.parent)
    staged = App(app.config, destination, staged=True)
    staged.clone(lambda message: None)
    assert GitRepo(staged.path).branch == "agent/test"
    promoted = staged.promote()
    assert GitRepo(promoted.path).branch == "agent/test"
    file = promoted.path / "custom" / "__init__.py"
    file.write_text("agent edits")
    with pytest.raises(BenchError, match="uncommitted edits"):
        GitRepo(promoted.path).remove()
    git(promoted.path, "restore", ".")
    GitRepo(promoted.path).remove()
    assert not promoted.path.exists()
    assert git(app.path, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(app.path, "rev-parse", "agent/test") == GitRepo(app.path).head_sha


def test_artifact_copies_relocate_links_and_do_not_share_writes(prepared, tmp_path):
    source, _ = prepared
    modules = source.app("frappe").path / "node_modules"
    modules.mkdir()
    (modules / "dependency.js").write_text("original")
    public = source.app("frappe").path / "frappe" / "public"
    public.mkdir()
    (public / "node_modules").symlink_to(modules)
    assets = source.sites_path / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "frappe").symlink_to(public)
    cache = BenchArtifacts(tmp_path / "cache")
    paths = cache.capture(source)
    target = Bench(BenchConfig.default("target"), tmp_path / "target")
    target.create_directories()
    cache.restore(target, paths)
    link = target.sites_path / "assets" / "frappe" / "node_modules"
    assert link.resolve() == target.apps_path / "frappe" / "node_modules"
    (link / "dependency.js").write_text("changed")
    assert (modules / "dependency.js").read_text() == "original"
    assert (cache.root / "apps/frappe/node_modules/dependency.js").read_text() == "original"


@pytest.mark.parametrize("cached", [True, False])
def test_prepared_node_dependencies_skip_yarn(prepared, fake_services, monkeypatch, cached):
    source, template = prepared
    (source.app("frappe").path / "package.json").write_text("{}")
    git(source.app("frappe").path, "add", "package.json")
    git(
        source.app("frappe").path,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "node dependencies",
    )
    data = json.loads((template / "template.json").read_text())
    data["apps"][0]["commit"] = GitRepo(source.app("frappe").path).head_sha
    data["artifacts"] = ["apps/frappe/node_modules"] if cached else []
    (template / "artifacts/apps/frappe/node_modules").mkdir(parents=True)
    (template / "template.json").write_text(json.dumps(data))
    installs = []
    monkeypatch.setattr(PythonEnvManager, "install_node_dependencies", lambda self: installs.append(True))
    source.fork("cached-node", template, lambda message: None)
    assert bool(installs) is not cached


def test_artifact_restore_overlaps_database_restore(prepared, fake_services, monkeypatch):
    source, template = prepared
    data = json.loads((template / "template.json").read_text())
    data["artifacts"] = ["sites/assets"]
    (template / "artifacts/sites/assets").mkdir(parents=True)
    (template / "template.json").write_text(json.dumps(data))
    artifacts_started = Event()
    restore_started = Event()
    original = SiteTemplate.restore_into

    def artifacts(self, bench, paths):
        artifacts_started.set()
        assert restore_started.wait(timeout=2), "database restore waited for artifact copies"

    def restore(self, bench, name):
        restore_started.set()
        assert artifacts_started.wait(timeout=2)
        return original(self, bench, name)

    monkeypatch.setattr(BenchArtifacts, "restore", artifacts)
    monkeypatch.setattr(SiteTemplate, "restore_into", restore)
    source.fork("overlap", template, lambda message: None)


def test_background_artifact_failure_marks_fork_failed(prepared, fake_services, monkeypatch):
    source, template = prepared
    data = json.loads((template / "template.json").read_text())
    data["artifacts"] = ["sites/assets"]
    (template / "artifacts/sites/assets").mkdir(parents=True)
    (template / "template.json").write_text(json.dumps(data))

    def fail(self, bench, paths):
        raise OSError("copy failed")

    monkeypatch.setattr(BenchArtifacts, "restore", fail)
    with pytest.raises(OSError, match="copy failed"):
        source.fork("copy-failed", template, lambda message: None)
    state = json.loads((source.path.parent / "copy-failed/fork.json").read_text())
    assert state["status"] == "failed"
    assert {"dependencies", "restore"}.issubset(state["timings"])


@pytest.mark.parametrize("selection", ["cwd", "flag", "explicit"])
def test_fork_command_resolves_source_and_target(prepared, monkeypatch, selection):
    from pilot.core.server import Server
    from pilot.internal.cli.dispatch import CliContext
    from pilot.internal.cli.registry import build_parser, dispatch

    source, _ = prepared
    root = source.path.parent.parent
    monkeypatch.chdir(source.app("frappe").path / "frappe" if selection == "cwd" else root)
    context = CliContext(root, bench_name="dev" if selection == "flag" else None)
    arguments = ["fork", "dev", "task-a"] if selection == "explicit" else ["fork", "task-a"]
    if selection == "explicit":
        arguments += ["--branch", "current", "--app-branches", "frappe=develop"]
    calls = []

    def copy(self, name, template, on_progress, *, site, branch, app_branches):
        assert branch == ("current" if selection == "explicit" else "default")
        assert app_branches == ({"frappe": "develop"} if selection == "explicit" else {})
        calls.append((self.path, name))
        return Bench(BenchConfig.default(name), self.path.parent / name)

    monkeypatch.setattr(Bench, "fork", copy)
    monkeypatch.setattr(Server, "bench", lambda self, name: source if name == "dev" else None)
    parser = build_parser()
    dispatch(parser.parse_args(arguments), parser, context)
    assert calls == [(source.path, "task-a")]


def test_fresh_fork_requires_site_selection_when_ambiguous(prepared, fake_services):
    source, _ = prepared
    with pytest.raises(BenchError, match="--site"):
        source.fork("ambiguous")


@pytest.mark.parametrize("source_changes", [False, True])
@pytest.mark.parametrize("live_artifacts", [False, True])
def test_preparation_records_exact_revisions_and_rejects_changes_during_backup(
    prepared,
    tmp_path,
    monkeypatch,
    source_changes,
    live_artifacts,
):
    from pilot.utils import run_command

    source, _ = prepared
    site = source.site("fixture.localhost")
    site.path.mkdir()
    (site.path / "site_config.json").write_text(json.dumps({"db_name": "fixture"}))
    modules = source.app("frappe").path / "node_modules"
    modules.mkdir()
    (modules / "dependency.js").write_text("current")
    destination = tmp_path / "new-template"
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: None)

    def backup(argv, **kwargs):
        if "backup" not in argv:
            return run_command(argv, **kwargs)
        for suffix in ("database.sql.gz", "files.tar", "private-files.tar", "site_config_backup.json"):
            (destination / f"20261001_120000-fixture-{suffix}").write_text("fixture")
        if source_changes:
            (source.app("custom").path / "custom/__init__.py").write_text("changed during backup")
        return None

    monkeypatch.setattr("pilot.core.site.template.run_command", backup)
    if source_changes:
        with pytest.raises(BenchError, match="Source changed"):
            SiteTemplate(destination).prepare(
                site, lambda message: None, reuse_source_artifacts=live_artifacts
            )
        assert not (destination / "template.json").exists()
    else:
        SiteTemplate(destination).prepare(site, lambda message: None, reuse_source_artifacts=live_artifacts)
        data = SiteTemplate(destination).read()
        assert {entry["name"]: entry["commit"] for entry in data["apps"]} == {
            app.config.name: GitRepo(app.path).head_sha for app in source.apps()
        }
        assert all(entry["repo"].startswith("https://example.com/") for entry in data["apps"])
        assert "apps/frappe/node_modules" in data["artifacts"]
        assert bool(data["artifact_source"]) is live_artifacts
        assert (destination / "artifacts").exists() is not live_artifacts


def test_live_artifacts_must_belong_to_source(prepared, tmp_path):
    source, template = prepared
    data = json.loads((template / "template.json").read_text())
    data["artifact_source"] = str(tmp_path / "unrelated")
    (template / "template.json").write_text(json.dumps(data))
    with pytest.raises(BenchError, match="belong to the source"):
        source.fork("wrong-artifacts", template)
    assert not (source.path.parent / "wrong-artifacts").exists()


def test_source_snapshot_reuses_running_redis_without_stopping_it(prepared, monkeypatch):
    from pilot.core.bench.cloning.runtime import ForkRuntime

    source, _ = prepared
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.redis_server_binary", lambda: "/redis")
    monkeypatch.setattr(ForkRuntime, "has_existing_redis", staticmethod(lambda port: True))

    def unexpected(*args, **kwargs):
        raise AssertionError("Running source Redis must not be replaced")

    monkeypatch.setattr("pilot.core.bench.cloning.runtime.RedisManager.generate_configs", unexpected)
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.subprocess.Popen", unexpected)
    with ForkRuntime(source, allow_existing=True):
        pass
