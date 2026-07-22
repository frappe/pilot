import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class RevertSiteTask(Task):
    """Chain link: restore one site's database and clear its cache, then queue the next site."""

    command: ClassVar[str] = "revert-site"

    operation_id: str
    site: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.revert_site(self.site, on_step=self.step, on_progress=self.report)
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.chain[-1]["task_id"])


if __name__ == "__main__":
    RevertSiteTask.main()
