from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.tasks import Arg, Task, step


@dataclass(kw_only=True)
class ReinstallSiteTask(Task):
    command: ClassVar[str] = "reinstall-site"

    site: str
    admin_password: Annotated[str, Arg(cli=False)]

    def run(self) -> None:
        self.reinstall()

    @step("reinstall", lambda self: f"Reinstall site {self.site}")
    def reinstall(self) -> None:
        self.bench.site(self.site).reinstall(self.admin_password)


if __name__ == "__main__":
    ReinstallSiteTask.main()
