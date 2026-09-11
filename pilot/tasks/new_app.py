from dataclasses import dataclass
from typing import ClassVar

from pilot.core.app import NewAppOptions
from pilot.tasks import Task, on_success, step


@dataclass(kw_only=True)
class NewAppTask(Task):
    """Scaffold a new Frappe app under apps/ and install it into the bench."""

    command: ClassVar[str] = "new-app"
    required_submit_args: ClassVar[tuple[str, ...]] = ("name", "description", "publisher", "email")
    # make-app writes into apps/ before the install lands, so a kill can leave a half-app.
    is_cancellable_while_running: ClassVar[bool] = False

    name: str = ""
    title: str = ""
    description: str = ""
    publisher: str = ""
    email: str = ""
    license: str = ""
    branch: str = ""
    github_workflow: bool = False

    def run(self) -> None:
        self.scaffold()

    @on_success
    def reload_workers(self) -> dict:
        """Workers hold the bench's app list and import map, so they need a
        restart once a new app is registered."""
        return {"web_only": False}

    @step("create", lambda self: f"Create {self.name}")
    def scaffold(self) -> None:
        options = NewAppOptions(
            title=self.title,
            description=self.description,
            publisher=self.publisher,
            email=self.email,
            license=self.license,
            branch=self.branch,
            github_workflow=self.github_workflow,
        )
        self.bench.new_app(self.name, options, on_progress=self.report)


if __name__ == "__main__":
    NewAppTask.main()
