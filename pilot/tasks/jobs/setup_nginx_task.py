from pilot.commands.setup.nginx import SetupNginxCommand
from .base_task import BaseTask


class SetupNginxTask(BaseTask):
    def run(self) -> None:
        self._step("nginx", "Set up Nginx")
        SetupNginxCommand(self.bench).run()
        self._step("done")


if __name__ == "__main__":
    SetupNginxTask.main()
