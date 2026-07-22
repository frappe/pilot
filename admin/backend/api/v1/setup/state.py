from __future__ import annotations

import logging
from pathlib import Path

from pilot.exceptions import TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.task import TaskReader, TaskStatus


def wizard_marker_path(bench_root: Path) -> Path:
    """Marker that keeps bootstrap on the wizard while setup is running."""
    return bench_root / ".wizard-active"


def running_setup_task(bench_root: Path):
    return next(
        (
            task
            for task in TaskReader(bench_root).list_tasks(limit=None)
            if task.command == "wizard-setup" and task.status.is_active
        ),
        None,
    )


def setup_handoff_task(bench_root: Path):
    marker = wizard_marker_path(bench_root)
    if not marker.exists():
        return running_setup_task(bench_root)

    task_id = marker.read_text(encoding="utf-8").strip()
    if task_id:
        try:
            task = TaskReader(bench_root).read_task(task_id)
        except TaskNotFoundError:
            return running_setup_task(bench_root)
        if task.command == "wizard-setup" and (task.status.is_active or task.status == TaskStatus.SUCCESS):
            return task
        return None

    return next(
        (
            task
            for task in TaskReader(bench_root).list_tasks(limit=None)
            if task.command == "wizard-setup" and (task.status.is_active or task.status == TaskStatus.SUCCESS)
        ),
        None,
    )


def clear_wizard_marker_if_idle(bench_root: Path) -> None:
    marker = wizard_marker_path(bench_root)
    try:
        with exclusive_file_lock(marker):
            if running_setup_task(bench_root) is None:
                marker.unlink(missing_ok=True)
    except Exception as exc:
        logging.debug("Could not clear the wizard marker for %s: %s", bench_root, exc)
