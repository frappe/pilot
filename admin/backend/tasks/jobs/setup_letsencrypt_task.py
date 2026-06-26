from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand
from .base_task import BaseTask


class SetupLetsEncryptTask(BaseTask):
    def run(self) -> None:
        SetupLetsEncryptCommand(self.bench).run()


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
