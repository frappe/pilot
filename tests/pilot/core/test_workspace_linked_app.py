from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.app.repository import AppRepository
from pilot.exceptions import BenchError
from pilot.managers.environment import PythonEnvManager
from pilot.managers.processes.local import ProcessManager
from pilot.managers.python_assets import PythonAssetBuilder
from tests.pilot.commands.test_commands import make_bench
from tests.pilot.core.test_workspace import make_linked_bench, make_repo


def test_linked_app_round_trips_through_bench_toml(tmp_path: Path) -> None:
    config = make_bench(tmp_path).config
    config.apps.append(AppConfig(name="gameplan", repo="", branch="", path="/src/gameplan"))
    config.write(tmp_path)

    assert 'path = "/src/gameplan"' in (tmp_path / "bench.toml").read_text()
    linked = BenchConfig.read(tmp_path).get_app_by_name("gameplan")
    assert linked.is_linked
    assert linked.path == "/src/gameplan"


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
