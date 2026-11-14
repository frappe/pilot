from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.core.site.provisioning import provision_from_backup
from pilot.tasks import Arg, Task, on_cancel, on_failure, on_success, step


@dataclass(kw_only=True)
class NewSiteFromBackupTask(Task):
    command: ClassVar[str] = "new-site-from-backup"
    timeout_seconds: ClassVar[int] = 86400

    name: str
    db_file: str
    admin_password: Annotated[str, Arg(cli=False)]
    public_files: str | None = None
    private_files: str | None = None

    def run(self) -> None:
        """Create the site from the backup."""
        self.require_production_privileges()
        self.restore()

    @on_failure
    @on_cancel
    def remove_failed_site(self) -> dict:
        """The callback args that remove the failed site."""
        return {"site": self.name}

    @on_success
    def release_site_creation(self) -> dict:
        """The callback args that release the site creation."""
        return {"site": self.name}

    @step("restore", lambda self: f"Restore site {self.name} from backup")
    def restore(self) -> None:
        """Create the site and restore the backup."""
        provision_from_backup(
            self.bench,
            self.name,
            self.db_file,
            admin_password=self.admin_password,
            public_files=self.public_files,
            private_files=self.private_files,
            on_progress=self.report,
        )


if __name__ == "__main__":
    NewSiteFromBackupTask.main()
