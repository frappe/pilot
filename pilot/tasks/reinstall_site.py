from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.tasks import Arg, Task, step


@dataclass(kw_only=True)
class ReinstallSiteTask(Task):
    command: ClassVar[str] = "reinstall-site"
    timeout_seconds: ClassVar[int] = 86400
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    admin_password: Annotated[str, Arg(cli=False)]

    @property
    def resource_keys(self) -> list[str]:
        """The resources this task holds while active."""
        return [self.get_site_resource_key(self.site)]

    def run(self) -> None:
        """Reinstall the site."""
        self.reinstall()

    @step("reinstall", lambda self: f"Reinstall site {self.site}")
    def reinstall(self) -> None:
        """Reinstall the site with the new password."""
        self.bench.site(self.site).reinstall(self.admin_password)


if __name__ == "__main__":
    ReinstallSiteTask.main()
