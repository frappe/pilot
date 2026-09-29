from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from pilot.core.site import Site
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RestoreSiteTask(Task):
    """Replace a site's database and files with a backup of any site on this bench."""

    command: ClassVar[str] = "restore-site"
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    source_site: str
    timestamp: str

    @cached_property
    def site_record(self) -> Site:
        return self.bench.site(self.site)

    def run(self) -> None:
        """A failed restore or migration leaves the site in maintenance mode, because
        its data can be partly restored or partly migrated."""
        original = self.site_record.maintenance_settings
        self.site_record.set_maintenance_mode(True)
        try:
            self.restore()
            self.migrate()
        except Exception:
            print(f"{self.site} stays in maintenance mode. Fix the error, then restore again.")
            raise
        self.site_record.set_maintenance_settings(original)

    @step("restore", lambda self: f"Restore {self.source_site} backup {self.timestamp} to {self.site}")
    def restore(self) -> None:
        files = self.bench.site(self.source_site).backups.get_restore_files(self.timestamp)
        self.site_record.restore(**files)

    @step("migrate", lambda self: f"Migrate site {self.site}")
    def migrate(self) -> None:
        self.site_record.migrate()


if __name__ == "__main__":
    RestoreSiteTask.main()
