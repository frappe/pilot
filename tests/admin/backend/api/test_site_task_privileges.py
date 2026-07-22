from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot.exceptions import BenchError
from pilot.managers.nginx import NginxManager
from pilot.tasks import Task
from tests.pilot.commands.test_commands import make_bench


def _task(tmp_path: Path, production: bool) -> Task:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    return Task(bench=bench, bench_root=tmp_path)


def test_production_site_task_fails_before_password_prompt(tmp_path: Path) -> None:
    task = _task(tmp_path, production=True)

    with (
        patch.object(NginxManager, "has_passwordless_sudo", new_callable=PropertyMock, return_value=False),
        pytest.raises(BenchError, match="non-interactive system privileges"),
    ):
        task.require_production_privileges()


def test_development_site_task_does_not_require_sudo(tmp_path: Path) -> None:
    task = _task(tmp_path, production=False)

    with patch.object(
        NginxManager, "has_passwordless_sudo", new_callable=PropertyMock
    ) as has_passwordless_sudo:
        task.require_production_privileges()

    has_passwordless_sudo.assert_not_called()
