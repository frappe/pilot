from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.core.site import provision_from_backup
from pilot.tasks import Arg, Task, step


@dataclass(kw_only=True)
class NewSiteFromBackupTask(Task):
    command: ClassVar[str] = "new-site-from-backup"

    name: str
    db_file: str
    admin_password: Annotated[str, Arg(cli=False)]
    public_files: str | None = None
    private_files: str | None = None

    def run(self) -> None:
        self.require_production_privileges()
        self.restore()

    @step("restore", lambda self: f"Restore site {self.name} from backup")
    def restore(self) -> None:
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
