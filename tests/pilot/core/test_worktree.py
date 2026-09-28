"""Worktree overlay layout, processes, removal and Frappe passthrough, on real git repos."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from pilot.config import BenchConfig, WorktreeConfig
from pilot.core.bench import Bench
from pilot.core.worktree import Worktree
from pilot.core.worktree.layout import WorktreeLayout
from pilot.core.worktree.processes import WorktreeProcessManager
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _make_app(apps_path: Path, name: str) -> None:
    path = apps_path / name
    (path / name / "public").mkdir(parents=True)
    (path / name / "__init__.py").write_text("")
    (path / name / "public" / ".gitkeep").write_text("")
    subprocess.run(["git", "init", "-q", "-b", "develop", str(path)], check=True)
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def make_bench(tmp_path: Path) -> Bench:
    bench_path = tmp_path / "benches" / "main"
    config = BenchConfig.from_flat("main", {"db_type": "sqlite"}, port_offset=21)
    bench_path.mkdir(parents=True)
    (bench_path / "bench.toml").write_text(config.dumps())
    bench = Bench(bench_path)
    bench.create_directories()
    bench.env_path.mkdir()
    for app in ("frappe", "gameplan"):
        _make_app(bench.apps_path, app)
    (bench.sites_path / "apps.txt").write_text("frappe\ngameplan\n")
    (bench.sites_path / "common_site_config.json").write_text(json.dumps({"developer_mode": 1}))
    (bench.sites_path / "assets" / "assets.json").write_text(
        json.dumps(
            {
                "frappe.bundle.js": "/assets/frappe/dist/js/frappe.bundle.FRESH.js",
                "gameplan.bundle.js": "/assets/gameplan/dist/js/gameplan.bundle.MAIN.js",
            }
        )
    )
    return bench


def add_checkout(bench: Bench) -> Worktree:
    with BenchConfig.open(bench.path) as config:
        config.worktrees.append(WorktreeConfig("feature-x", "gameplan", "gp.localhost", 1))
    worktree = Bench(bench.path).worktree("feature-x")
    GitRepo(bench.apps_path / "gameplan").add_worktree(worktree.app_path, "feature-x")
    WorktreeLayout(worktree).sync()
    return worktree


def test_layout_links_main_apps_and_keeps_its_own_ports_and_assets(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    worktree = add_checkout(bench)
    overlay = worktree.path
    (overlay / "apps" / "removed-app").symlink_to(tmp_path / "missing")
    (overlay / "sites" / "assets" / "assets.json").write_text(
        json.dumps(
            {
                "frappe.bundle.js": "/assets/frappe/dist/js/frappe.bundle.STALE.js",
                "gameplan.bundle.js": "/assets/gameplan/dist/js/gameplan.bundle.OWN.js",
            }
        )
    )
    (worktree.app_path / "node_modules").mkdir()

    WorktreeLayout(worktree).sync()

    assert (overlay / "apps" / "frappe").resolve() == bench.apps_path / "frappe"
    assert not (overlay / "apps" / "gameplan").is_symlink()
    assert not (overlay / "apps" / "removed-app").is_symlink()
    assert (overlay / "env").resolve() == bench.env_path
    assert (
        overlay / "sites" / "assets" / "frappe"
    ).resolve() == bench.apps_path / "frappe" / "frappe" / "public"
    assert (overlay / "sites" / "assets" / "gameplan").resolve() == worktree.app_path / "gameplan" / "public"
    assert (
        worktree.app_path / "gameplan" / "public" / "node_modules"
    ).resolve() == worktree.app_path / "node_modules"
    assert not (bench.apps_path / "frappe" / "frappe" / "public" / "node_modules").exists()
    assert not (overlay / "bench.toml").exists()
    assert (overlay / "sites" / "apps.txt").read_text() == "frappe\ngameplan\n"
    site_config = json.loads((overlay / "sites" / "common_site_config.json").read_text())
    assert site_config["developer_mode"] == 1
    assert site_config["webserver_port"] == 8001
    assert site_config["redis_cache"] == "redis://localhost:13001"
    assert site_config["redis_queue"] == "redis://localhost:11001"
    assert json.loads((overlay / "sites" / "assets" / "assets.json").read_text()) == {
        "frappe.bundle.js": "/assets/frappe/dist/js/frappe.bundle.FRESH.js",
        "gameplan.bundle.js": "/assets/gameplan/dist/js/gameplan.bundle.OWN.js",
    }


def test_processes_run_from_the_overlay_without_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pilot.core.worktree.processes.get_yarn_bin", lambda: "yarn")
    worktree = add_checkout(make_bench(tmp_path))
    bundle = worktree.app_path / "gameplan" / "public" / "js" / "gameplan.bundle.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("")
    (worktree.app_path / "frontend").mkdir()
    (worktree.app_path / "frontend" / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
    manager = WorktreeProcessManager(worktree)

    manager.write_config()
    definitions = {pd.name: pd for pd in manager._process_definitions()}

    assert "admin" not in definitions
    assert "port 13001" in (worktree.path / "config" / "redis_cache.conf").read_text()
    assert definitions["redis_queue"].argv[-1] == str(worktree.path / "config" / "redis_queue.conf")
    for pd in definitions.values():
        assert pd.env["PYTHONPATH"] == str(worktree.app_path)
        assert pd.env["FRAPPE_BENCH_ROOT"] == str(worktree.path)
    assert definitions["socketio"].argv[:3] == ["node", "--preserve-symlinks", "--preserve-symlinks-main"]
    assert definitions["watch"].argv == ["yarn", "run", "watch", "--apps", "gameplan"]
    assert definitions["watch"].working_dir == worktree.path / "apps" / "frappe"
    assert definitions["frontend"].critical is False
    assert "--strictPort" in definitions["frontend"].argv
    assert definitions["web"].argv[-2:] == ["--port", "8001"]


def test_build_runs_esbuild_for_the_app_only_from_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pilot.core.worktree.get_yarn_bin", lambda: "yarn")
    worktree = add_checkout(make_bench(tmp_path))

    with patch("pilot.managers.python_assets.PythonAssetBuilder.run_compiler") as run_compiler:
        worktree.build_assets()

    run_compiler.assert_called_once()
    assert run_compiler.call_args.args[0] == [
        "yarn",
        "run",
        "build",
        "--apps",
        "gameplan",
        "--run-build-command",
    ]
    assert run_compiler.call_args.kwargs["cwd"] == worktree.path / "apps" / "frappe"
    assert run_compiler.call_args.kwargs["env"]["FRAPPE_BENCH_ROOT"] == str(worktree.path)


def test_stop_signals_only_the_runner_in_the_pid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = add_checkout(make_bench(tmp_path))
    listening = MagicMock(return_value={4242})
    monkeypatch.setattr("pilot.managers.processes.local._pids_listening", listening)

    with patch("os.kill") as kill, pytest.raises(BenchError, match="not running"):
        worktree.stop()
    kill.assert_not_called()

    WorktreeProcessManager(worktree).pid_file.write_text("777")
    with patch("os.kill", side_effect=[None, ProcessLookupError]) as kill:
        worktree.stop()

    assert kill.call_args_list == [call(777, signal.SIGTERM), call(777, 0)]
    listening.assert_not_called()
    assert worktree.runtime_bench.config.admin.port == 0


def test_remove_refuses_a_dirty_worktree_and_leaves_everything_in_place(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    worktree = add_checkout(bench)
    (worktree.app_path / "notes.txt").write_text("work in progress")

    with pytest.raises(BenchError, match="uncommitted changes"):
        worktree.remove()

    assert (worktree.app_path / "notes.txt").exists()
    assert BenchConfig.read(bench.path).worktrees
    assert GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")


def test_remove_drops_checkout_overlay_record_and_optionally_the_branch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    worktree = add_checkout(bench)

    worktree.remove(delete_branch=True)

    assert not worktree.path.exists()
    assert (bench.apps_path / "frappe" / "frappe").is_dir()
    assert bench.env_path.is_dir()
    assert BenchConfig.read(bench.path).worktrees == []
    assert not GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")


@pytest.mark.parametrize(
    "args",
    [["build"], ["--site", "x", "watch"], ["build", "--app", "frappe"], ["watch", "--apps", "gameplan"]],
)
def test_frappe_passthrough_refuses_asset_commands(tmp_path: Path, args: list[str]) -> None:
    worktree = Worktree(make_bench(tmp_path), WorktreeConfig("feature-x", "gameplan", "gp.localhost", 1))

    with patch("subprocess.run") as run, pytest.raises(BenchError, match="worktree start"):
        worktree.frappe(args)
    run.assert_not_called()


def test_frappe_passthrough_runs_in_the_overlay_with_worktree_code(tmp_path: Path) -> None:
    worktree = Worktree(make_bench(tmp_path), WorktreeConfig("feature-x", "gameplan", "gp.localhost", 1))

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert worktree.frappe(["--site", "x", "migrate"]) == 0

    argv = run.call_args.args[0]
    assert argv[-3:] == ["--site", "x", "migrate"]
    assert run.call_args.kwargs["cwd"] == worktree.path / "sites"
    assert run.call_args.kwargs["env"]["PYTHONPATH"] == str(worktree.app_path)
    assert run.call_args.kwargs["env"]["PATH"] == os.environ["PATH"]
