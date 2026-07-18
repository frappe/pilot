"""Tests for WizardSetupTask — the setup wizard's init + production hand-off."""

from __future__ import annotations

from unittest.mock import MagicMock

from pilot.tasks.wizard_setup import WizardSetupTask


def _make_task(process_manager: str) -> WizardSetupTask:
    bench = MagicMock()
    bench.config.production.process_manager = process_manager
    return WizardSetupTask(bench=bench, bench_root=bench.path)


def test_run_skips_production_setup_for_plain_dev_bench() -> None:
    task = _make_task("")

    task.run()

    task.bench.initialize.assert_called_once()
    task.bench.setup_production.assert_not_called()


def test_run_finishes_production_setup_when_process_manager_chosen() -> None:
    """Wizard setup finishes production when a process manager was chosen."""
    task = _make_task("systemd")

    task.run()

    task.bench.initialize.assert_called_once()
    task.bench.setup_production.assert_called_once_with(
        best_effort_tls=True, on_progress=task.report
    )
