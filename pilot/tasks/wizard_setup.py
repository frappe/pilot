from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class WizardSetupTask(Task):
    """Initialize a bench, then finish production setup when preselected."""

    command: ClassVar[str] = "wizard-setup"

    def run(self) -> None:
        self.init()
        if self.bench.config.production.process_manager:
            self.setup_production()

    @step("init", "Initialize bench")
    def init(self) -> None:
        self.bench.initialize(on_progress=self.report)

    @step("production", "Set up production")
    def setup_production(self) -> None:
        # Wizard handoff tolerates pending DNS; the CLI path fails hard on TLS.
        self.bench.setup_production(best_effort_tls=True, on_progress=self.report)


if __name__ == "__main__":
    WizardSetupTask.main()
