"""Tests for NewAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.core.app import NewAppOptions
from pilot.core.bench import Bench
from pilot.tasks.new_app import NewAppTask
from tests.pilot.commands.test_commands import make_bench


def make_task(bench_root: Path, **overrides) -> NewAppTask:
    bench = make_bench(bench_root)
    bench.create_directories()
    args = {
        "name": "people",
        "title": "People",
        "description": "People operations",
        "publisher": "Frappe",
        "email": "people@example.com",
        "license": "mit",
        "branch": "develop",
    }
    args.update(overrides)
    return NewAppTask(bench=bench, bench_root=bench_root, **args)


def test_scaffold_answers_make_app_prompts_from_task_args(tmp_path: Path) -> None:
    task = make_task(tmp_path)

    with patch.object(Bench, "new_app") as new_app:
        task.scaffold()

    name, options = new_app.call_args.args
    assert name == "people"
    assert options == NewAppOptions(
        title="People",
        description="People operations",
        publisher="Frappe",
        email="people@example.com",
        license="mit",
        branch="develop",
        github_workflow=False,
    )


def test_scaffold_carries_the_github_workflow_choice(tmp_path: Path) -> None:
    task = make_task(tmp_path, github_workflow=True)

    with patch.object(Bench, "new_app") as new_app:
        task.scaffold()

    assert new_app.call_args.args[1].github_workflow is True


def test_blank_optional_answers_stay_blank_so_frappe_defaults_apply(tmp_path: Path) -> None:
    task = make_task(tmp_path, title="", license="", branch="")

    with patch.object(Bench, "new_app") as new_app:
        task.scaffold()

    options = new_app.call_args.args[1]
    assert (options.title, options.license, options.branch) == ("", "", "")
