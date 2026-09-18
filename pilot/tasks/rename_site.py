from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RenameSiteTask(Task):
    """Rename a site and refresh whatever serves it, without dropping a request."""

    command: ClassVar[str] = "rename-site"
    # A partial rename can leave the site under a name nothing references.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    new_name: str
    # Keep the old hostname by default so existing links continue to work.
    keep_old_hostname: bool = True

    def run(self) -> None:
        self.require_production_privileges()
        self.rename_site()

    @step("rename", lambda self: f"Rename {self.site} to {self.new_name}")
    def rename_site(self) -> None:
        self.bench.site(self.site).rename_to(
            self.new_name,
            on_progress=self.report,
            keep_old_hostname=self.keep_old_hostname,
        )


if __name__ == "__main__":
    RenameSiteTask.main()
