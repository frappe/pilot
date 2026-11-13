from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class BuildTask(Task):
    command: ClassVar[str] = "build"

    app: str | None = None
    site: str = ""

    def run(self) -> None:
        """Build the assets."""
        self.build()

    @step("build", lambda self: self.build_label)
    def build(self) -> None:
        """Build the assets of a site, an app or the bench."""
        if self.site:
            self.bench.site(self.site).build_assets()
            return
        self.bench.rebuild_assets(apps=[self.app] if self.app else None, force=True)

    @property
    def build_label(self) -> str:
        """The label of the build step."""
        if self.site:
            return f"Build assets for {self.site}"
        return f"Build assets for {self.app}" if self.app else "Build assets"


if __name__ == "__main__":
    BuildTask.main()
