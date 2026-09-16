from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


def _build_step_label(task: "BuildTask") -> str:
    """Generate display label for asset compilation step."""
    if task.site and task.app:
        return f"Build assets for {task.app} on {task.site}"
    if task.site:
        return f"Build assets for {task.site}"
    if task.app:
        return f"Build assets for {task.app}"
    return "Build assets"


@dataclass(kw_only=True)
class BuildTask(Task):
    """Task to build frontend assets for a bench or site."""

    command: ClassVar[str] = "build"

    app: str | None = None
    site: str | None = None
    force: bool = False

    def run(self) -> None:
        self.build()

    @step("build", _build_step_label)
    def build(self) -> None:
        """Rebuild frontend assets for a site or the whole bench."""
        if self.site:
            self.bench.site(self.site).build_assets(app=self.app, force=self.force)
        else:
            apps = [self.app] if self.app else None
            self.bench.rebuild_assets(apps=apps, force=self.force)


if __name__ == "__main__":
    BuildTask.main()
