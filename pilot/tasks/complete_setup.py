from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class CompleteSetupTask(Task):
    """Set up a new site for its first user, as Frappe's setup wizard would."""

    command: ClassVar[str] = "complete-setup"

    site: str
    full_name: str
    email: str
    language: str = ""
    country: str = ""
    time_zone: str = ""
    currency: str = ""

    def run(self) -> None:
        self.complete_setup()

    @step("complete_setup", lambda self: f"Complete setup for {self.site}")
    def complete_setup(self) -> None:
        answers = {
            "full_name": self.full_name,
            "email": self.email,
            "language": self.language,
            "country": self.country,
            "timezone": self.time_zone,
            "currency": self.currency,
        }
        self.bench.site(self.site).complete_setup({key: value for key, value in answers.items() if value})


if __name__ == "__main__":
    CompleteSetupTask.main()
