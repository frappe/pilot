from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand
from .base_task import BaseTask


class SetupLetsEncryptTask(BaseTask):
    def run(self) -> None:
        self._step("letsencrypt", "Set up Let's Encrypt")
        SetupLetsEncryptCommand(self.bench).run()
        self._step("done")


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
