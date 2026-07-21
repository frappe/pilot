from __future__ import annotations

from enum import StrEnum


class MigrationState(StrEnum):
    PREPARING = "preparing"
    UPDATING = "updating"
    MIGRATING = "migrating"
    NEEDS_ATTENTION = "needs_attention"
    RETRYING = "retrying"
    REVERTING = "reverting"
    COMPLETED = "completed"
    REVERTED = "reverted"
    REVERT_FAILED = "revert_failed"


TERMINAL_STATES = frozenset({MigrationState.COMPLETED, MigrationState.REVERTED})

# States where the operation still owns its safeguards and may need the user.
UNRESOLVED_STATES = frozenset(MigrationState) - TERMINAL_STATES

_ALLOWED: dict[MigrationState, set[MigrationState]] = {
    MigrationState.PREPARING: {
        MigrationState.UPDATING,
        MigrationState.MIGRATING,
        MigrationState.NEEDS_ATTENTION,
    },
    MigrationState.UPDATING: {MigrationState.MIGRATING, MigrationState.NEEDS_ATTENTION},
    MigrationState.MIGRATING: {MigrationState.COMPLETED, MigrationState.NEEDS_ATTENTION},
    MigrationState.NEEDS_ATTENTION: {MigrationState.RETRYING, MigrationState.REVERTING},
    MigrationState.RETRYING: {
        MigrationState.MIGRATING,
        MigrationState.COMPLETED,
        MigrationState.NEEDS_ATTENTION,
    },
    MigrationState.REVERTING: {MigrationState.REVERTED, MigrationState.REVERT_FAILED},
    MigrationState.REVERT_FAILED: {MigrationState.REVERTING},
    MigrationState.COMPLETED: set(),
    MigrationState.REVERTED: set(),
}


class MigrationStateError(Exception):
    pass


def validate_transition(current: str, target: str) -> None:
    if MigrationState(target) not in _ALLOWED.get(MigrationState(current), set()):
        raise MigrationStateError(f"Illegal migration transition: {current} -> {target}")
