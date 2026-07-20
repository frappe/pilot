from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetupProductionTask(Task):
    command: ClassVar[str] = "setup-production"

    process_manager: str | None = None
    admin_domain: str | None = None
    tls: bool = False
    letsencrypt_email: str | None = None

    def run(self) -> None:
        self.setup_production()

    @step("production", "Set up production")
    def setup_production(self) -> None:
        self.bench.setup_production(
            process_manager=self.process_manager,
            admin_domain=self.admin_domain,
            admin_tls=self.tls,
            letsencrypt_email=self.letsencrypt_email,
            on_progress=self.report,
        )


if __name__ == "__main__":
    SetupProductionTask.main()
