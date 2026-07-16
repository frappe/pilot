from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.tasks.jobs.base_task import BaseTask
from pilot.exceptions import BenchError
from tests.test_commands import make_bench


def _task(tmp_path: Path, production: bool) -> BaseTask:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    return BaseTask(bench, tmp_path, SimpleNamespace())


def test_production_site_task_fails_before_password_prompt(tmp_path: Path) -> None:
    task = _task(tmp_path, production=True)

    with (
        patch(
            "pilot.tasks.jobs.base_task.has_passwordless_sudo",
            return_value=False,
        ),
        pytest.raises(BenchError, match="non-interactive system privileges"),
    ):
        task._require_production_privileges()


def test_development_site_task_does_not_require_sudo(tmp_path: Path) -> None:
    task = _task(tmp_path, production=False)

    with patch(
        "pilot.tasks.jobs.base_task.has_passwordless_sudo"
    ) as has_passwordless_sudo:
        task._require_production_privileges()

    has_passwordless_sudo.assert_not_called()
