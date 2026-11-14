from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_success, step


@dataclass(kw_only=True)
class RemoveAppTask(Task):
    command: ClassVar[str] = "remove-app"

    name: str

    def run(self) -> None:
        """Remove the app and record it."""
        self.remove()
        self.bench.audit_action("app", {"event": "removed", "app": self.name})

    @on_success
    def reload_workers(self) -> dict:
        """Long-lived web and background workers hold the old app list and
        import map, so they need a restart once this task lands."""
        return {"web_only": False}

    @step("remove", lambda self: f"Remove {self.name}")
    def remove(self) -> None:
        """Remove the app from the bench."""
        self.bench.app(self.name).remove(force=True, on_progress=self.report)


if __name__ == "__main__":
    RemoveAppTask.main()
