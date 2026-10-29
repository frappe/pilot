from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.core.site.provisioning import SiteProvisioner
from pilot.core.workspace import Workspace
from pilot.exceptions import BenchError, ConfigError
from pilot.internal.cli.dispatch import CliContext, find_bench_root
from pilot.internal.patch_state import is_applied
from pilot.managers.environment import PythonEnvManager
from pilot.managers.redis import RedisManager
from tests.pilot.commands.test_commands import make_bench


@pytest.fixture
def benches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "benches"
    path.mkdir()
    monkeypatch.setattr("pilot.core.workspace.benches_dir", lambda: path)
    return path


def make_repo(path: Path, name: str = "gameplan", dev_script: bool = True) -> Path:
    (path / name).mkdir(parents=True)
    (path / name / "hooks.py").write_text(f"app_name = '{name}'\n")
    (path / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    scripts = {"dev": "vite"} if dev_script else {}
    (path / "package.json").write_text(json.dumps({"scripts": scripts}))
    return path


def make_linked_bench(path: Path, repo: Path) -> Bench:
    """A bench whose bench.toml links `repo` into apps/gameplan."""
    bench = make_bench(path)
    bench.create_directories()
    app_config = AppConfig(name="gameplan", repo="", branch="", path=str(repo))
    bench.config.apps.append(app_config)
    App(app_config, bench).link()
    return bench


def fake_initialize(bench: Bench, *args, **kwargs) -> None:
    bench.create_directories()


def fake_provision(bench: Bench, name: str, apps: list[str], admin_password: str, **_) -> Site:
    site = bench.site(name)
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")
    return site


def test_workspace_is_found_from_inside_the_repository(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "Projects" / "gameplan")
    nested = repo / "gameplan" / "api"
    nested.mkdir()

    workspace = Workspace.find(nested)

    assert workspace is not None
    assert workspace.root == repo
    assert workspace.app_name == "gameplan"
    assert workspace.find_bench() is None
    assert Workspace.find(tmp_path) is None


def test_an_app_without_a_project_name_is_named_after_its_hooks_package(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "gameplan-repo")
    (repo / "pyproject.toml").write_text("[build-system]\nrequires = []\n")

    workspace = Workspace.find(repo)

    assert workspace is not None
    assert workspace.app_name == "gameplan"
    (repo / "pyproject.toml").write_text("[project\n")
    assert Workspace(repo).app_name == "gameplan"
    (repo / "crm").mkdir()
    (repo / "crm" / "hooks.py").write_text("")
    assert Workspace.find(repo) is None


def test_the_bench_recorded_in_the_git_directory_is_looked_up_first(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    bench = make_linked_bench(benches / "dev-gameplan", repo)
    bench.config.write(bench.path)
    (benches / "dev-broken").mkdir()
    (benches / "dev-broken" / "bench.toml").write_text("[bench\nname = 'dev-broken'\n")
    (benches / "dev-crm").mkdir()
    (benches / "dev-crm" / "bench.toml").write_text('[[apps]]\nname = "crm"\npath = 123\n')
    (benches / "dev-hrms").mkdir()
    (benches / "dev-hrms" / "bench.toml").write_text("apps = ['hrms']\n")
    record = repo / ".git" / "pilot-bench"

    found = Workspace(repo).find_bench()
    record.write_text("dev-gone\n")
    found_past_a_stale_record = Workspace(repo).find_bench()

    assert found is not None and found.path == bench.path
    assert found_past_a_stale_record is not None and found_past_a_stale_record.path == bench.path
    record.write_text("dev-broken\n")
    with pytest.raises(ConfigError, match=r"dev-broken/bench\.toml"):
        Workspace(repo).find_bench()


def test_a_bench_linking_the_repository_through_a_symlink_is_found(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "Projects" / "gameplan")
    (tmp_path / "code").symlink_to(tmp_path / "Projects", target_is_directory=True)
    bench = make_linked_bench(benches / "dev-gameplan", tmp_path / "code" / "gameplan")
    bench.config.write(bench.path)

    found = Workspace(repo).find_bench()

    assert found is not None
    assert found.path == bench.path


def test_another_bench_linking_a_symlink_loop_does_not_stop_the_lookup(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    (benches / "dev-crm").mkdir()
    (benches / "dev-crm" / "bench.toml").write_text(f'[[apps]]\nname = "crm"\npath = "{loop}"\n')
    bench = make_linked_bench(benches / "dev-gameplan", repo)
    bench.config.write(bench.path)
    resolve = Path.resolve

    def resolve_like_python_3_11(path: Path, strict: bool = False) -> Path:
        if path == loop:
            raise RuntimeError(f"Symlink loop from {path}")
        return resolve(path, strict)

    with patch.object(Path, "resolve", resolve_like_python_3_11):
        found = Workspace(repo).find_bench()

    assert found is not None
    assert found.path == bench.path


@pytest.mark.parametrize("engine", ["mariadb", "postgres"])
def test_setup_clones_source_site_and_inherits_database(tmp_path, benches, engine):
    source_bench = make_bench(benches / "source")
    source_bench.create_directories()
    source_bench.config.write(source_bench.path)
    source_site = source_bench.site("source.localhost")
    source_site.path.mkdir()
    (source_site.path / "site_config.json").write_text(json.dumps({"db_type": engine}))
    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    def clone(site, name, destination, password, on_progress):
        assert site.path == source_site.path
        assert password == "admin"
        return fake_provision(destination, name, [], password)

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "clone", autospec=True, side_effect=clone) as copied,
        patch.object(Site, "provision") as provision,
        patch.object(Workspace, "sync") as sync,
    ):
        site = workspace.setup(from_site="source/source.localhost")
        assert workspace.bench.config.db_type == engine
        assert json.loads((site.path / "site_config.json").read_text())["developer_mode"] == 1
        copied.assert_called_once()
        provision.assert_not_called()
        sync.assert_called_once()
        with pytest.raises(BenchError, match="already exists"):
            workspace.setup(from_site="source/source.localhost")
        copied.assert_called_once()
    assert is_applied(workspace.bench.path, "enroll_sites_in_scheduled_backups")


@pytest.mark.parametrize(
    "value,engine,database,message",
    [
        ("source", "mariadb", None, "BENCH/SITE"),
        ("source/missing.localhost", "mariadb", None, "does not exist"),
        ("source/source.localhost", "sqlite", None, "MariaDB and PostgreSQL"),
        ("source/source.localhost", "mariadb", "postgres", "not 'postgres'"),
    ],
)
def test_invalid_clone_source_does_not_create_workspace_bench(
    tmp_path, benches, value, engine, database, message
):
    source_bench = make_bench(benches / "source")
    source_bench.create_directories()
    source_bench.config.write(source_bench.path)
    site = source_bench.site("source.localhost")
    site.path.mkdir()
    (site.path / "site_config.json").write_text(json.dumps({"db_type": engine}))
    workspace = Workspace(make_repo(tmp_path / "gameplan"))
    with pytest.raises(BenchError, match=message):
        workspace.setup(from_site=value, db_type=database)
    assert not (benches / "dev-gameplan").exists()


@pytest.mark.parametrize("paused", [False, True])
def test_sync_installs_missing_app_without_enabling_paused_scheduler(tmp_path, benches, paused):
    repo = make_repo(tmp_path / "gameplan")
    bench = make_linked_bench(benches / "dev-gameplan", repo)
    workspace = Workspace(repo)
    workspace.bench = bench
    site = workspace.site
    site.path.mkdir()
    flags = {"pause_scheduler": 1, "mute_emails": 1} if paused else {}
    (site.path / "site_config.json").write_text(json.dumps(flags))

    with (
        patch.object(Bench, "install_requirements"),
        patch.object(RedisManager, "running"),
        patch.object(Site, "installed_apps", return_value=["frappe"]),
        patch.object(Site, "install_app") as install,
        patch.object(Site, "finish_provisioning") as finish,
        patch.object(Site, "enable_scheduler") as enable,
        patch.object(Site, "migrate"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
    ):
        workspace.sync(lambda message: None)
    assert install.call_count == int(paused)
    assert finish.call_count == int(not paused)
    enable.assert_not_called()
    assert json.loads((site.path / "site_config.json").read_text()) == flags


def test_setup_creates_a_linked_bench_and_reruns_only_sync_it(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    workspace = Workspace(repo)

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize) as initialize,
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision) as provision,
        patch.object(Bench, "install_requirements") as install_requirements,
        patch.object(Site, "installed_apps", return_value=["frappe", "gameplan"]),
        patch.object(Site, "install_app") as install_app,
        patch.object(Site, "migrate") as migrate,
        patch.object(RedisManager, "running") as redis_running,
        patch.object(PythonEnvManager, "build_assets_for_app") as build_assets,
    ):
        site = workspace.setup()
        bench = workspace.bench
        (bench.env_path / "bin").mkdir(parents=True)
        bench.python.touch()
        workspace.setup()

    assert bench.path == benches / "dev-gameplan"
    assert (repo / ".git" / "pilot-bench").read_text() == "dev-gameplan\n"
    assert bench.config.db_type == "sqlite"
    assert bench.config.framework_app.branch == "develop"
    assert not bench.config.admin.enabled
    assert bench.config.get_app_by_name("gameplan").path == str(repo)
    assert site.config.name == "dev-gameplan.localhost"
    assert json.loads((site.path / "site_config.json").read_text()) == {"developer_mode": 1}
    common_site_config = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert common_site_config["default_site"] == "dev-gameplan.localhost"
    initialize.assert_called_once_with(bench, on_progress=ANY)
    provision.assert_called_once()
    assert provision.call_args.kwargs["schedule_backups"] is False
    assert is_applied(bench.path, "enroll_sites_in_scheduled_backups")
    install_requirements.assert_called_once()
    install_app.assert_not_called()
    migrate.assert_called_once()
    redis_running.assert_called_once()
    assert [call.args[0].config.name for call in build_assets.call_args_list] == ["gameplan"]

    with pytest.raises(BenchError, match="uses database 'sqlite', not 'postgres'"):
        workspace.setup(db_type="postgres")


def test_setup_records_the_requested_frappe_branch_and_rejects_an_invalid_one(
    tmp_path: Path, benches: Path
) -> None:
    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup(frappe_branch="version-15")
    with pytest.raises(BenchError, match=r"must not contain '\.\.'"):
        Workspace(make_repo(tmp_path / "crm", name="crm")).setup(frappe_branch="bad..branch")

    assert BenchConfig.read(workspace.bench.path).framework_app.branch == "version-15"
    assert not (benches / "dev-crm" / "bench.toml").exists()


def test_setup_resumes_an_interrupted_initialization(tmp_path: Path, benches: Path) -> None:
    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    def interrupted_initialize(bench: Bench, *args, **kwargs) -> None:
        fake_initialize(bench)
        (bench.path / ".initializing").touch()
        (bench.env_path / "bin").mkdir(parents=True)
        bench.python.touch()
        raise KeyboardInterrupt

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=interrupted_initialize),
        pytest.raises(KeyboardInterrupt),
    ):
        workspace.setup()
    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=RuntimeError("clone failed")),
        pytest.raises(BenchError, match="could not be resumed: clone failed\nRun 'pilot dev drop'"),
    ):
        workspace.setup()
    with (
        patch.object(Bench, "initialize", autospec=True) as initialize,
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup()

    initialize.assert_called_once_with(workspace.bench, on_progress=ANY)
    assert workspace.site.exists


def test_setup_after_a_finished_initialization_goes_straight_to_the_site(
    tmp_path: Path, benches: Path
) -> None:
    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    def finished_initialize(bench: Bench, *args, **kwargs) -> None:
        fake_initialize(bench)
        (bench.env_path / "bin").mkdir(parents=True)
        bench.python.touch()

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=finished_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=BenchError("database is down")),
        pytest.raises(BenchError, match="database is down"),
    ):
        workspace.setup()
    assert is_applied(workspace.bench.path, "enroll_sites_in_scheduled_backups")
    with (
        patch.object(Bench, "initialize", autospec=True) as initialize,
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup()

    initialize.assert_not_called()
    assert workspace.site.exists


def test_setup_resumes_a_site_left_without_the_app(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")

    def provision_without_app(bench: Bench, name: str, *args, **kwargs) -> Site:
        fake_provision(bench, name, *args, **kwargs)
        raise BenchError("install-app failed")

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=provision_without_app),
        pytest.raises(BenchError, match="install-app failed"),
    ):
        Workspace(repo).setup()

    workspace = Workspace(repo)
    bench = workspace.bench
    assert bench.config.get_app_by_name("gameplan").is_linked
    assert not bench.config.admin.enabled
    (bench.env_path / "bin").mkdir(parents=True)
    bench.python.touch()

    with (
        patch.object(Bench, "install_requirements"),
        patch.object(Bench, "write_common_site_config"),
        patch.object(Site, "installed_apps", return_value=["frappe"]),
        patch.object(Site, "install_app") as install_app,
        patch.object(Site, "enable_scheduler") as enable_scheduler,
        patch.object(Site, "migrate") as migrate,
        patch.object(SiteProvisioner, "write_pilot_communication_config"),
        patch.object(SiteProvisioner, "add_to_hosts"),
        patch.object(SiteProvisioner, "reload_nginx"),
        patch.object(RedisManager, "running"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
    ):
        workspace.setup()

    assert install_app.call_args.args[0].config.name == "gameplan"
    enable_scheduler.assert_called_once_with()
    migrate.assert_called_once()


def test_setup_after_remove_app_links_and_installs_the_app_again(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    workspace = Workspace(repo)
    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup()
    bench = workspace.bench
    (bench.env_path / "bin").mkdir(parents=True)
    bench.python.touch()
    with patch.object(App, "_uninstall_from_all_sites"), patch.object(App, "_pip_uninstall"):
        bench.app("gameplan").remove()

    with (
        patch.object(PythonEnvManager, "install_app") as install_into_environment,
        patch.object(Bench, "install_requirements"),
        patch.object(Bench, "write_common_site_config"),
        patch.object(Site, "installed_apps", return_value=["frappe"]),
        patch.object(Site, "install_app") as install_app,
        patch.object(Site, "enable_scheduler"),
        patch.object(Site, "migrate") as migrate,
        patch.object(SiteProvisioner, "write_pilot_communication_config"),
        patch.object(SiteProvisioner, "add_to_hosts"),
        patch.object(SiteProvisioner, "reload_nginx"),
        patch.object(RedisManager, "running"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
    ):
        workspace.setup()

    assert (bench.apps_path / "gameplan").resolve() == repo
    assert "gameplan" in bench.registered_apps()
    assert install_into_environment.call_args.args[0].config.name == "gameplan"
    assert install_app.call_args.args[0].config.name == "gameplan"
    migrate.assert_called_once()


def test_start_and_drop_wait_for_the_bench_to_stop(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    workspace = Workspace(repo)
    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup()
    bench = workspace.bench
    (bench.env_path / "bin").mkdir(parents=True)
    bench.python.touch()
    (bench.path / ".initializing").touch()
    with patch.object(Bench, "start"), pytest.raises(BenchError, match="not set up yet"):
        workspace.start()
    (bench.path / ".initializing").unlink()
    bench.pids_path.mkdir(parents=True, exist_ok=True)
    (bench.pids_path / "bench.pid").write_text(str(os.getpid()))

    with pytest.raises(BenchError, match="already running"):
        workspace.start()
    with pytest.raises(BenchError, match="is running"):
        workspace.drop()

    assert workspace.site.exists
    assert bench.path.exists()

    (bench.pids_path / "bench.pid").unlink()
    with patch.object(Site, "drop"), patch.object(Bench, "drop"):
        workspace.drop()

    assert not (repo / ".git" / "pilot-bench").exists()


def test_a_broken_git_link_leaves_the_working_tree_untouched(tmp_path: Path, benches: Path) -> None:
    repo = make_repo(tmp_path / "gameplan")
    shutil.rmtree(repo / ".git")
    (repo / ".git").write_text(f"gitdir: {tmp_path / 'missing'}\n")
    (repo / "pilot-bench").write_text("the developer's own file\n")
    workspace = Workspace(repo)

    with (
        patch.object(Bench, "initialize", autospec=True, side_effect=fake_initialize),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(Site, "provision", side_effect=fake_provision),
    ):
        workspace.setup()
    with patch.object(Site, "drop"), patch.object(Bench, "drop"):
        workspace.drop()

    assert workspace.bench_record is None
    assert (repo / "pilot-bench").read_text() == "the developer's own file\n"


def test_a_second_checkout_gets_its_own_bench(tmp_path: Path, benches: Path) -> None:
    (benches / "dev-gameplan").mkdir()

    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    assert workspace.next_bench_name == "dev-gameplan-2"


def test_bench_commands_inside_the_repository_use_its_bench(
    tmp_path: Path, benches: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "gameplan")
    bench_path = benches / "dev-gameplan"
    config = make_bench(bench_path).config
    config.apps.append(AppConfig(name="gameplan", repo="", branch="", path=str(repo)))
    bench_path.mkdir()
    config.write(bench_path)
    monkeypatch.chdir(repo / "gameplan")

    root = find_bench_root(CliContext(installation_root=tmp_path / "pilot"))

    assert root == bench_path
