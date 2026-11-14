from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class UninstallAppTask(Task):
    command: ClassVar[str] = "uninstall-app"
    # frappe writes module defs and doctypes before recording the app as
    # installed, so a kill mid-run leaves rows behind that fail every retry.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    app: str
    force: bool = False
    remove_from_bench: bool = True

    def run(self) -> None:
        """Uninstall the app."""
        self.uninstall()

    @step("uninstall", lambda self: f"Uninstall {self.app} from {self.site}")
    def uninstall(self) -> None:
        """Uninstall the app from the site in maintenance mode."""
        site = self.bench.site(self.site)
        with site.under_maintenance():
            site.uninstall_apps(
                [self.app], force=self.force, on_progress=self.report, remove_from_bench=self.remove_from_bench
            )
        site.clear_cache()


if __name__ == "__main__":
    UninstallAppTask.main()
