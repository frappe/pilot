from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


# Module-level function (not a method) because the @step decorator's label
# argument expects a callable(task) that is resolved before the class body
# is fully defined.  A forward-reference string type hint is sufficient for
# static analysis; it is never evaluated at runtime.
def _build_step_label(task: "BuildTask") -> str:
    if task.site and task.app:
        return f"Build assets for {task.app} on {task.site}"
    if task.site:
        return f"Build assets for {task.site}"
    if task.app:
        return f"Build assets for {task.app}"
    return "Build assets"


@dataclass(kw_only=True)
class BuildTask(Task):
    command: ClassVar[str] = "build"

    app: str | None = None
    site: str | None = None
    # Default False preserves backward compatibility with bench-wide CLI builds.
    # The admin API passes force=True explicitly for user-triggered site builds.
    force: bool = False

    def run(self) -> None:
        self.build()

    @step("build", _build_step_label)
    def build(self) -> None:
        if self.site:
            self.bench.site(self.site).build_assets(app=self.app, force=self.force)
        else:
            apps = [self.app] if self.app else None
            self.bench.rebuild_assets(apps=apps, force=self.force)


if __name__ == "__main__":
    BuildTask.main()

