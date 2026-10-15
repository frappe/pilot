from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.app.repository import AppRepository
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.core.site.provisioning import SiteProvisioner
from pilot.core.workspace import Workspace
from pilot.exceptions import BenchError, ConfigError
from pilot.internal.cli.dispatch import CliContext, find_bench_root
from pilot.managers.environment import PythonEnvManager
from pilot.managers.processes.local import ProcessManager
from pilot.managers.python_assets import PythonAssetBuilder
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


def test_linked_app_round_trips_through_bench_toml(tmp_path: Path) -> None:
    config = make_bench(tmp_path).config
    config.apps.append(AppConfig(name="gameplan", repo="", branch="", path="/src/gameplan"))
    config.write(tmp_path)

    assert 'path = "/src/gameplan"' in (tmp_path / "bench.toml").read_text()
    linked = BenchConfig.read(tmp_path).get_app_by_name("gameplan")
    assert linked.is_linked
    assert linked.path == "/src/gameplan"


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
    initialize.assert_called_once()
    provision.assert_called_once()
    install_requirements.assert_called_once()
    install_app.assert_not_called()
    migrate.assert_called_once()
    redis_running.assert_called_once()
    assert [call.args[0].config.name for call in build_assets.call_args_list] == ["gameplan"]

    with pytest.raises(BenchError, match="uses database 'sqlite', not 'postgres'"):
        workspace.setup(db_type="postgres")


def test_setup_resumes_an_interrupted_initialization(tmp_path: Path, benches: Path) -> None:
    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    def interrupted_initialize(bench: Bench, *args, **kwargs) -> None:
        fake_initialize(bench)
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

    initialize.assert_called_once()
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


def test_a_second_checkout_gets_its_own_bench(tmp_path: Path, benches: Path) -> None:
    (benches / "dev-gameplan").mkdir()

    workspace = Workspace(make_repo(tmp_path / "gameplan"))

    assert workspace.new_bench_name() == "dev-gameplan-2"


def test_linked_app_is_a_symlink_whose_git_state_pilot_never_touches(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "src" / "gameplan")
    bench = make_linked_bench(tmp_path / "bench", repo)
    (bench.apps_path / "helpdesk").symlink_to(make_repo(tmp_path / "src" / "helpdesk", name="helpdesk"))

    assert (bench.apps_path / "gameplan").resolve() == repo
    assert bench.app("gameplan").is_linked
    assert not bench.app("helpdesk").is_linked
    with pytest.raises(BenchError, match="is linked to"):
        bench.app("gameplan").update()


def test_an_update_saves_no_revision_for_a_linked_app_so_a_revert_skips_it(tmp_path: Path) -> None:
    bench = make_linked_bench(tmp_path / "bench", make_repo(tmp_path / "src" / "gameplan"))
    make_repo(bench.apps_path / "helpdesk", name="helpdesk")

    with patch.object(AppRepository, "update_target", return_value=None) as update_target:
        operation = bench.migrations.create_update()

    assert [revision.name for revision in operation.apps] == ["helpdesk"]
    assert bench.app("gameplan").update_target() is None
    update_target.assert_called_once()


def test_removing_a_linked_app_unlinks_it_and_keeps_the_checkout(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "src" / "gameplan")
    bench = make_linked_bench(tmp_path / "bench", repo)
    (bench.sites_path / "apps.txt").write_text("frappe\ngameplan\n")

    with patch.object(App, "_pip_uninstall"):
        bench.app("gameplan").remove()

    assert not (bench.apps_path / "gameplan").is_symlink()
    assert (repo / "gameplan" / "hooks.py").exists()
    assert bench.registered_apps() == ["frappe"]


def test_linked_app_builds_from_source_with_the_bench_path(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "src" / "gameplan")
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package.json").write_text("{}")
    bench = make_linked_bench(tmp_path / "bench", repo)
    builder = PythonAssetBuilder(PythonEnvManager(bench))

    with (
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets") as prebuilt,
        patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name: name),
        patch("pilot.core.app.node_packages.run_command"),
        patch.object(PythonAssetBuilder, "run_compiler") as run_compiler,
    ):
        builder.build_assets_for_app(bench.app("gameplan"))

    prebuilt.assert_not_called()
    assert [call.args[0][-1] for call in run_compiler.call_args_list] == ["gameplan", "build"]
    for call in run_compiler.call_args_list:
        assert call.kwargs["env"]["FRAPPE_BENCH_PATH"] == str(bench.path)


def test_the_bench_config_decides_admin_and_the_app_dev_script(tmp_path: Path) -> None:
    linked = make_linked_bench(tmp_path / "linked", make_repo(tmp_path / "src" / "gameplan"))
    linked.config.admin.enabled = False
    plain = make_bench(tmp_path / "plain")
    plain.config.admin.enabled = True

    with patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name, **_: name):
        linked_processes = {pd.name: pd for pd in ProcessManager(linked)._process_definitions()}
        plain_processes = {pd.name for pd in ProcessManager(plain)._process_definitions()}

    assert "admin" not in linked_processes
    assert {"web", "socketio", "watch"} <= linked_processes.keys()
    assert linked_processes["gameplan-dev"].argv == ["yarn", "run", "dev"]
    assert linked_processes["gameplan-dev"].working_dir == linked.apps_path / "gameplan"
    assert linked_processes["gameplan-dev"].env == {
        "FRAPPE_BENCH_PATH": str(linked.path),
        "FRAPPE_WEB_SERVER_PORT": str(linked.config.http_port),
    }
    assert "admin" in plain_processes
    assert not any(name.endswith("-dev") for name in plain_processes)


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
