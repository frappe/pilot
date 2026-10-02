from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.tasks import Arg, Task, step


@dataclass(kw_only=True)
class CloneSiteTask(Task):
    command: ClassVar[str] = "clone-site"
    is_cancellable_while_running: ClassVar[bool] = False
    site: str
    name: str
    target_bench: str
    admin_password: Annotated[str, Arg(cli=False)]

    @step("clone", lambda self: f"Clone {self.site} into {self.name}")
    def run(self) -> None:
        from pilot.core.bench import Bench

        destination = Bench(self.bench.path.parent / self.target_bench)
        from pilot.exceptions import BenchError
        from pilot.managers.nginx import NginxManager

        if destination.config.production.enabled and not NginxManager(destination).has_passwordless_sudo:
            raise BenchError("Production site operations require non-interactive system privileges.")
        self.bench.site(self.site).clone(self.name, destination, self.admin_password, self.report)


if __name__ == "__main__":
    CloneSiteTask.main()
