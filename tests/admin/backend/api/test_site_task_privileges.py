from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.tasks import BaseTask
from pilot.exceptions import BenchError
from tests.pilot.commands.test_commands import make_bench


def _task(tmp_path: Path, production: bool) -> BaseTask:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    return BaseTask(bench=bench, bench_root=tmp_path)


def test_production_site_task_fails_before_password_prompt(tmp_path: Path) -> None:
    task = _task(tmp_path, production=True)

    with (
        patch("pilot.managers.platform.has_passwordless_sudo", return_value=False),
        pytest.raises(BenchError, match="non-interactive system privileges"),
    ):
        task.require_production_privileges()


def test_development_site_task_does_not_require_sudo(tmp_path: Path) -> None:
    task = _task(tmp_path, production=False)

    with patch("pilot.managers.platform.has_passwordless_sudo") as has_passwordless_sudo:
        task.require_production_privileges()

    has_passwordless_sudo.assert_not_called()
