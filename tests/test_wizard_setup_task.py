"""Tests for WizardSetupTask — the setup wizard's init + production hand-off."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from admin.backend.tasks.jobs.wizard_setup_task import WizardSetupTask


def _make_task(process_manager: str) -> WizardSetupTask:
    bench = MagicMock()
    bench.config.production.process_manager = process_manager
    return WizardSetupTask(bench, bench.path, MagicMock())


def test_run_skips_production_setup_for_plain_dev_bench() -> None:
    task = _make_task("")

    with patch("pilot.commands.init.InitCommand.run") as mock_init, \
         patch("pilot.commands.setup.production.SetupProductionCommand.run") as mock_setup:
        task.run()

    mock_init.assert_called_once()
    mock_setup.assert_not_called()


def test_run_finishes_production_setup_when_process_manager_chosen() -> None:
    """A bench created via the admin UI's "New Bench" dialog already has a
    process manager recorded, but production.enabled stays false until the
    venv/framework app this task's init step installs actually exist.
    SetupProductionCommand then finishes the job (workload, nginx, TLS,
    persisting production.enabled) the same way `bench setup production`
    would from the CLI, instead of duplicating those steps here. TLS is
    best-effort here (unlike the CLI): nobody's watching to retry by hand if
    a cert can't issue yet, so a DNS hiccup must not roll back the rest."""
    task = _make_task("systemd")

    with patch("pilot.commands.init.InitCommand.run") as mock_init, \
         patch("pilot.commands.setup.production.SetupProductionCommand") as mock_cls:
        task.run()

    mock_init.assert_called_once()
    mock_cls.assert_called_once_with(task.bench, best_effort_tls=True)
    mock_cls.return_value.run.assert_called_once()
