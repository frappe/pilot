from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RefreshStorageUsageTask(Task):
    """Re-measures every site on the bench: they share one report."""

    command: ClassVar[str] = "refresh-storage-usage"

    @step("measure", "Measuring every site's files and database")
    def run(self) -> None:
        self.bench.site_storage.collect()


if __name__ == "__main__":
    RefreshStorageUsageTask.main()
