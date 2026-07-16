from pilot.commands.setup.production import SetupProductionCommand
from pilot.tasks.jobs.base_task import BaseTask


class SetupProductionTask(BaseTask):
    def run(self) -> None:
        self._step("production", "Set up production")
        SetupProductionCommand(self.bench).run()
        self._step("done")


if __name__ == "__main__":
    SetupProductionTask.main()
