from __future__ import annotations

import pytest

from pilot.tasks.manager.task_state import (
    ACTIVE_TASK_STATUSES,
    ALLOWED_TASK_TRANSITIONS,
    TERMINAL_TASK_STATUSES,
    TaskStatus,
    parse_task_status,
    validate_task_transition,
)


def test_task_states_define_the_complete_lifecycle() -> None:
    assert set(TaskStatus) == {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.KILLED,
    }
    assert TERMINAL_TASK_STATUSES == {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.KILLED,
    }
    assert ACTIVE_TASK_STATUSES == {TaskStatus.QUEUED, TaskStatus.RUNNING}


def test_task_transitions_are_defined_for_every_state() -> None:
    assert set(ALLOWED_TASK_TRANSITIONS) == set(TaskStatus)
    assert ALLOWED_TASK_TRANSITIONS[TaskStatus.QUEUED] == {
        TaskStatus.RUNNING,
        TaskStatus.KILLED,
    }
    assert ALLOWED_TASK_TRANSITIONS[TaskStatus.RUNNING] == TERMINAL_TASK_STATUSES


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


@pytest.mark.parametrize("terminal", TERMINAL_TASK_STATUSES)
def test_terminal_task_cannot_transition(terminal: TaskStatus) -> None:
    with pytest.raises(ValueError, match="Invalid task transition"):
        validate_task_transition(terminal, TaskStatus.RUNNING)


def test_unknown_task_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown task status"):
        parse_task_status("waiting")
