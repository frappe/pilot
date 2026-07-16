from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.tasks.jobs.update_cli_task import UpdateCliTask


def make_task(bench_root: Path) -> UpdateCliTask:
    return UpdateCliTask(MagicMock(), bench_root, MagicMock())


def test_run_pulls_the_cli_root(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    with patch("pilot.tasks.jobs.update_cli_task.cli_root", return_value=tmp_path), patch(
        "subprocess.run"
    ) as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        task.run()

    run.assert_called_once_with(["git", "-C", str(tmp_path), "pull"], check=True)


def test_run_raises_on_git_failure(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    with patch("pilot.tasks.jobs.update_cli_task.cli_root", return_value=tmp_path), patch(
        "subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")
    ):
        with pytest.raises(subprocess.CalledProcessError):
            task.run()
