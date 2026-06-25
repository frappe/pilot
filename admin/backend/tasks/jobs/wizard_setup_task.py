from __future__ import annotations

from bench_cli.commands.init import InitCommand

from .base_task import BaseTask


class WizardSetupTask(BaseTask):
    """Initialize the bench — the only thing the setup wizard does.

    The wizard gets a development-ready bench up; production is a deliberate,
    separate step the user runs from the terminal afterwards (`bench setup
    production --admin-domain ... --tls`). Keeping the wizard to init alone
    gives the frontend one continuous stream to follow and one state to resume.
    """

    def run(self) -> None:
        InitCommand(self.bench).run()


if __name__ == "__main__":
    WizardSetupTask.main()
