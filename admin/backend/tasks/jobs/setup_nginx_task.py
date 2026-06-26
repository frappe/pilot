from pilot.commands.setup.nginx import SetupNginxCommand
from .base_task import BaseTask


class SetupNginxTask(BaseTask):
    def run(self) -> None:
        SetupNginxCommand(self.bench).run()


if __name__ == "__main__":
    SetupNginxTask.main()
