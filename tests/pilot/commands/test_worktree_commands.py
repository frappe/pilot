"""`pilot worktree` commands: parsing, and adding a worktree against real git repos."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pilot.commands.worktree.add import AddWorktreeCommand
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.worktree import Worktree
from pilot.exceptions import BenchError
from pilot.internal.cli.command import command_from_args
from pilot.internal.cli.registry import build_parser
from pilot.internal.git import GitRepo
from tests.pilot.core.test_worktree import make_bench


def _add_site(bench: Bench, name: str, apps: list[str]) -> None:
    site = bench.sites_path / name
    (site / "db").mkdir(parents=True)
    (site / "site_config.json").write_text(
        json.dumps({"db_name": "_db", "db_type": "sqlite", "installed_apps": apps})
    )
    sqlite3.connect(site / "db" / "_db.db").close()


def _parse(*argv: str):
    args = build_parser().parse_args(list(argv))
    return command_from_args(args._command_cls, args, bench=None)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    monkeypatch.setattr("pilot.core.bench.ports._port_is_live", lambda port: False)
    monkeypatch.setattr(Worktree, "build_assets", lambda self: None)
    bench = make_bench(tmp_path)
    _add_site(bench, "gp.localhost", ["frappe", "gameplan"])
    _add_site(bench, "other.localhost", ["frappe"])
    return bench


def test_add_and_frappe_parse_their_arguments() -> None:
    add = _parse("worktree", "add", "gameplan", "feature-x", "--site", "gp.localhost", "--from", "v1")
    passthrough = _parse("worktree", "frappe", "feature-x", "--site", "x", "list-apps")

    assert (add.app_name, add.worktree_name, add.site, add.branch, add.start_point) == (
        "gameplan",
        "feature-x",
        "gp.localhost",
        "",
        "v1",
    )
    assert passthrough.args == ("--site", "x", "list-apps")


def test_add_infers_the_base_site_and_names_the_branch_after_the_worktree(bench: Bench) -> None:
    AddWorktreeCommand(bench=bench, app_name="gameplan", worktree_name="feature-x").run()

    worktree = Bench(bench.path).worktree("feature-x")
    assert worktree.config.base_site == "gp.localhost"
    assert worktree.config.port_offset == 0
    assert worktree.branch == "feature-x"
    assert (worktree.path / "sites" / "feature-x.gp.localhost" / "site_config.json").exists()


def test_add_refuses_an_ambiguous_base_site_before_creating_anything(bench: Bench) -> None:
    _add_site(bench, "second.localhost", ["frappe", "gameplan"])

    with pytest.raises(BenchError, match="Several sites"):
        AddWorktreeCommand(bench=bench, app_name="gameplan", worktree_name="feature-x").run()

    assert not (bench.path / "worktrees").exists()
    assert not GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")


def test_failed_add_is_undone(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: Worktree) -> None:
        raise BenchError("build failed")

    monkeypatch.setattr(Worktree, "build_assets", fail)

    with pytest.raises(BenchError, match="build failed"):
        AddWorktreeCommand(bench=bench, app_name="gameplan", worktree_name="feature-x").run()

    assert not (bench.path / "worktrees" / "feature-x").exists()
    assert BenchConfig.read(bench.path).worktrees == []
    assert not GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")
