from __future__ import annotations

import pytest

from pilot.internal.tasks.state import parse_task_status, validate_task_transition
from pilot.managers.task.models import TaskStatus


def test_task_states_define_the_complete_lifecycle() -> None:
    assert set(TaskStatus) == {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.KILLED,
    }


def test_task_status_reports_active_and_terminal_groups() -> None:
    assert {status for status in TaskStatus if status.is_active} == {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    }
    assert {status for status in TaskStatus if status.is_terminal} == {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.KILLED,
    }


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.QUEUED, TaskStatus.KILLED),
        (TaskStatus.RUNNING, TaskStatus.SUCCESS),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.KILLED),
    ],
)
def test_valid_task_transition_is_accepted(
    current: TaskStatus,
    target: TaskStatus,
) -> None:
    validate_task_transition(current, target)


@pytest.mark.parametrize(
    "terminal",
    [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED],
)
def test_terminal_task_cannot_transition(terminal: TaskStatus) -> None:
    with pytest.raises(ValueError, match="Invalid task transition"):
        validate_task_transition(terminal, TaskStatus.RUNNING)


def test_unknown_task_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown task status"):
        parse_task_status("waiting")
