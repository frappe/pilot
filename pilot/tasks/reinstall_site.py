from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.core.site import Site

from pilot.tasks.base import Arg, BaseTask, step


@dataclass(kw_only=True)
class ReinstallSiteTask(BaseTask):
    command: ClassVar[str] = "reinstall-site"

    site: str
    admin_password: Annotated[str, Arg(cli=False)]

    def run(self) -> None:
        self.reinstall()

    @step("reinstall", lambda self: f"Reinstall site {self.site}")
    def reinstall(self) -> None:
        Site.for_name(self.site, self.bench).reinstall(self.admin_password)


if __name__ == "__main__":
    ReinstallSiteTask.main()
