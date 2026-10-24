from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.core.bench.migration.recovery import reconcile_migration


@dataclass(kw_only=True)
class RecoverMigrationCommand(Command):
    name: ClassVar[str] = "migration-recover"
    group: ClassVar[str] = "tasks"
    help: ClassVar[str] = "Reconcile an interrupted migration without rerunning it."

    operation_id: Annotated[str, Arg(help="Migration operation ID.")]
    force: Annotated[bool, Arg(help="Reconcile an orphaned running task after verification.")] = False

    def run(self) -> None:
        if self.force:
            self.confirm("Force an orphaned migration to Needs Attention?")
        operation = reconcile_migration(self.bench, self.operation_id, force=self.force)
        self.report(f"Migration {operation.id}: {operation.state}")


@dataclass(kw_only=True)
class StopMigrationCommand(Command):
    name: ClassVar[str] = "migration-stop"
    group: ClassVar[str] = "tasks"
    help: ClassVar[str] = "Stop a running migration task after process verification."

    operation_id: Annotated[str, Arg(help="Migration operation ID.")]

    def run(self) -> None:
        from pilot.core.bench.migration.recovery import stop_migration

        self.confirm("Terminate the migration task? Database changes may be partial.")
        operation = stop_migration(self.bench, self.operation_id)
        self.report(f"Migration {operation.id}: {operation.state}")
