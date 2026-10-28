import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_cancel, on_failure


@dataclass(kw_only=True)
class RevertSiteTask(Task):
    """Chain link: restore one site's database and clear its cache, then queue the next site."""

    command: ClassVar[str] = "revert-site"
    timeout_seconds: ClassVar[int] = 86400

    operation_id: str
    site: str

    def run(self) -> None:
        operation = self.bench.migrations.get_for_chain_task(
            self.operation_id, self.running_task_id, self.command, {"site": self.site}
        )
        try:
            operation.revert_site(self.site, on_step=self.step, on_progress=self.report)
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.chain[-1]["task_id"])

    @on_failure
    @on_cancel
    def fail_migration_step(self) -> dict:
        return {"operation_id": self.operation_id, "phase": "reverting_sites", "site": self.site}


if __name__ == "__main__":
    RevertSiteTask.main()
