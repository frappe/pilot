from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class ChangeAdminDomainTask(Task):
    """Move the admin to a new hostname, reissuing TLS and nginx behind it."""

    command: ClassVar[str] = "change-admin-domain"
    # Do not leave a partially switched route cancellable.
    is_cancellable_while_running: ClassVar[bool] = False

    domain: str
    tls: bool | None = None

    def run(self) -> None:
        self.require_production_privileges()
        self.change_domain()

    @step("domain", lambda self: f"Move the admin to {self.domain}")
    def change_domain(self) -> None:
        self.bench.change_admin_domain(self.domain, self.tls, on_progress=self.report)


if __name__ == "__main__":
    ChangeAdminDomainTask.main()
