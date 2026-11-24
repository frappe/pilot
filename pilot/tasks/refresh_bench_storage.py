from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RefreshBenchStorageTask(Task):
    """Re-measures the bench folder for the storage page, off the request."""

    command: ClassVar[str] = "refresh-bench-storage"
    static_resource_keys: ClassVar[tuple[str, ...]] = ("bench-storage",)
    timeout_seconds: ClassVar[int] = 900
    # A page load queues it whenever the report is old; keep it out of the audit log and task list.
    audit_on_queue: ClassVar[bool] = False
    is_listed: ClassVar[bool] = False

    @step("measure", "Measuring the bench folder", icon="lucide-hard-drive")
    def run(self) -> None:
        """Measure the bench folder and save the report."""
        self.bench.storage.collect()


if __name__ == "__main__":
    RefreshBenchStorageTask.main()
