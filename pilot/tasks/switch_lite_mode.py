from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SwitchLiteModeTask(Task):
    """Rebuild the process set after lite mode is turned on or off. A task rather
    than part of the settings request: reinstalling the units restarts admin."""

    command: ClassVar[str] = "switch-lite-mode"

    def run(self) -> None:
        """Switch lite mode."""
        self.rebuild_process_set()

    @step("processes", "Rebuild the process set")
    def rebuild_process_set(self) -> None:
        """Reinstall the processes for the new mode."""
        self.bench.rebuild_process_set(on_progress=self.report)


if __name__ == "__main__":
    SwitchLiteModeTask.main()
