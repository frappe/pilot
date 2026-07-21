from __future__ import annotations

PREPARING = "preparing"
UPDATING = "updating"
MIGRATING = "migrating"
NEEDS_ATTENTION = "needs_attention"
RETRYING = "retrying"
RESTORING = "restoring"
COMPLETED = "completed"
RESTORED = "restored"
RESTORE_FAILED = "restore_failed"

STATES = frozenset(
    {
        PREPARING,
        UPDATING,
        MIGRATING,
        NEEDS_ATTENTION,
        RETRYING,
        RESTORING,
        COMPLETED,
        RESTORED,
        RESTORE_FAILED,
    }
)

TERMINAL_STATES = frozenset({COMPLETED, RESTORED})

# States where the operation still owns its safeguards and may need the user.
UNRESOLVED_STATES = STATES - TERMINAL_STATES

_ALLOWED: dict[str, set[str]] = {
    PREPARING: {UPDATING, MIGRATING, NEEDS_ATTENTION},
    UPDATING: {MIGRATING, NEEDS_ATTENTION},
    MIGRATING: {COMPLETED, NEEDS_ATTENTION},
    NEEDS_ATTENTION: {RETRYING, RESTORING},
    RETRYING: {MIGRATING, COMPLETED, NEEDS_ATTENTION},
    RESTORING: {RESTORED, RESTORE_FAILED},
    RESTORE_FAILED: {RESTORING},
    COMPLETED: set(),
    RESTORED: set(),
}


class MigrationStateError(Exception):
    pass


def validate_transition(current: str, target: str) -> None:
    if target not in _ALLOWED.get(current, set()):
        raise MigrationStateError(f"Illegal migration transition: {current} -> {target}")
