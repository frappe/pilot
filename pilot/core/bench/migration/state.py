from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pilot.core.bench.migration.operation import MigrationOperation

ChainStep = tuple[str, dict]


class MigrationStateError(Exception):
    pass


class MigrationState:
    """A GoF state object owning its allowed transitions and next chain task; compares and serializes by `name`."""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    is_terminal: ClassVar[bool] = False
    is_failure: ClassVar[bool] = False  # paused on a failure, waiting for the user
    starts_work: ClassVar[bool] = False
    allowed: ClassVar[frozenset[str]] = frozenset()

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        """The (task_command, args) to queue next, or None to pause the chain."""
        return None

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"MigrationState({self.name!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MigrationState):
            return self.name == other.name
        return self.name == other

    def __hash__(self) -> int:
        return hash(self.name)


class Preparing(MigrationState):
    name = "preparing"
    label = "Preparing"
    allowed = frozenset({"backing_up", "updating", "migrating", "needs_attention"})


class BackingUp(MigrationState):
    name = "backing_up"
    label = "Backing up"
    starts_work = True
    allowed = frozenset({"updating", "migrating", "needs_attention"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        site = operation.next_backup_site()
        return ("migration-backup", {"site": site}) if site else None


class Updating(MigrationState):
    name = "updating"
    label = "Updating apps"
    starts_work = True
    allowed = frozenset({"migrating", "needs_attention"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        return None if operation.apps_updated else ("update", {})


class Migrating(MigrationState):
    name = "migrating"
    label = "Migrating"
    starts_work = True
    allowed = frozenset({"completed", "needs_attention"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        site = operation.next_migrate_site()
        return ("migrate", {"site": site}) if site else None


class NeedsAttention(MigrationState):
    name = "needs_attention"
    label = "Needs attention"
    is_failure = True
    allowed = frozenset({"retrying", "reverting_apps", "reverting_sites", "restarting"})


class Retrying(MigrationState):
    name = "retrying"
    label = "Retrying"
    allowed = frozenset({"backing_up", "updating", "migrating"})


class RevertingApps(MigrationState):
    name = "reverting_apps"
    label = "Reverting app revisions"
    starts_work = True
    allowed = frozenset({"reverting_sites", "restarting", "revert_failed"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        return None if operation.revert_checkpoints.get("apps") else ("revert-apps", {})


class RevertingSites(MigrationState):
    name = "reverting_sites"
    label = "Recovering sites"
    starts_work = True
    allowed = frozenset({"restarting", "revert_failed"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        site = operation.next_revert_site()
        return ("revert-site", {"site": site}) if site else None


class Restarting(MigrationState):
    name = "restarting"
    label = "Restarting services"
    starts_work = True
    allowed = frozenset({"reverted", "revert_failed"})

    def next_step(self, operation: "MigrationOperation") -> ChainStep | None:
        return None if operation.revert_checkpoints.get("restarted") else ("restart-services", {})


class RevertFailed(MigrationState):
    name = "revert_failed"
    label = "Revert failed"
    is_failure = True
    allowed = frozenset({"reverting_apps", "reverting_sites", "restarting"})


class Completed(MigrationState):
    name = "completed"
    label = "Completed"
    is_terminal = True


class Reverted(MigrationState):
    name = "reverted"
    label = "Reverted"
    is_terminal = True


_STATES: dict[str, MigrationState] = {
    state.name: state
    for state in (
        Preparing(),
        BackingUp(),
        Updating(),
        Migrating(),
        NeedsAttention(),
        Retrying(),
        RevertingApps(),
        RevertingSites(),
        Restarting(),
        RevertFailed(),
        Completed(),
        Reverted(),
    )
}


def get_state(name: str | MigrationState) -> MigrationState:
    key = name.name if isinstance(name, MigrationState) else name
    try:
        return _STATES[key]
    except KeyError:
        raise MigrationStateError(f"Unknown migration state: {name!r}") from None


def validate_transition(current: str | MigrationState, target: str | MigrationState) -> None:
    source = get_state(current)
    goal = get_state(target)
    if goal.name not in source.allowed:
        raise MigrationStateError(f"Illegal migration transition: {source.name} -> {goal.name}")
