from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RefreshStorageUsageTask(Task):
    """Re-measures every site on the bench: they share one report."""

    command: ClassVar[str] = "refresh-storage-usage"
    static_resource_keys: ClassVar[tuple[str, ...]] = ("site-storage",)
    timeout_seconds: ClassVar[int] = 300

    @step("measure", "Measuring every site's files and database", icon="lucide-shield-check")
    def run(self) -> None:
        """Measure the site storage."""
        self.bench.site_storage.collect()


if __name__ == "__main__":
    RefreshStorageUsageTask.main()
