from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class RenameSiteTask(Task):
    """Move a site to a new hostname via Site.rename_to."""

    command: ClassVar[str] = "rename-site"
    # A rename moves the site directory and rewrites bench config; killing it
    # mid-run leaves the site half-moved.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    new_name: str

    def run(self) -> None:
        self.require_production_privileges()
        self.rename()

    @step("rename", lambda self: f"Rename site {self.site} to {self.new_name}")
    def rename(self) -> None:
        self.bench.site(self.site).rename_to(self.new_name, on_progress=self.report)


if __name__ == "__main__":
    RenameSiteTask.main()
