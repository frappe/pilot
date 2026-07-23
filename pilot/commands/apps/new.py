from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError, CommandError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App, NewAppOptions


@dataclass(kw_only=True)
class NewAppCommand(Command):
    name: ClassVar[str] = "new-app"
    help: ClassVar[str] = "Create a new Frappe app under the apps folder."

    LICENSES: ClassVar[tuple[str, ...]] = (
        "agpl-3.0", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "bsl-1.0", "cc0-1.0",
        "epl-2.0", "gpl-2.0", "gpl-3.0", "lgpl-2.1", "mit", "mpl-2.0", "unlicense",
    )

    app_name: Annotated[str, Arg(help="Name for the new app.", metavar="app")]
    title: Annotated[str, Arg(help="App title (defaults to the app name).")] = ""
    description: Annotated[str, Arg(help="App description.")] = ""
    publisher: Annotated[str, Arg(help="App publisher.")] = ""
    email: Annotated[str, Arg(help="Publisher email.")] = ""
    license: Annotated[str, Arg(help="App license (default: mit).")] = ""
    branch: Annotated[str, Arg(help="Initial git branch (default: develop).")] = ""
    github_workflow: Annotated[bool, Arg(help="Add a GitHub Actions unittest workflow.")] = False

    def run(self) -> None:
        from pilot.core.app import App

        if not App.is_available_on_bench(self.bench, self.app_name):
            raise BenchError(f"App '{self.app_name}' already exists in this bench.")
        self.app: "App" = self.bench.new_app(self.app_name, self._collect_options(), on_progress=self.report)

    def _collect_options(self) -> "NewAppOptions":
        from pilot.core.app import NewAppOptions

        default_title = self.app_name.replace("-", " ").replace("_", " ").title()
        return NewAppOptions(
            title=self._ask("App Title", self.title, default_title),
            description=self._ask("App Description", self.description, "", required=True),
            publisher=self._ask("App Publisher", self.publisher, self._git_config("user.name") or "Frappe"),
            email=self._ask("App Email", self.email, self._git_config("user.email"), required=True),
            license=self._ask(f"App License ({', '.join(self.LICENSES)})", self.license, "mit"),
            github_workflow=self._confirm("Create GitHub Workflow action for unittests", self.github_workflow),
            branch=self._ask("Branch Name", self.branch, "develop"),
        )

    def _ask(self, prompt: str, value: str, default: str, *, required: bool = False) -> str:
        if value:
            return value
        if not sys.stdin.isatty():
            if required and not default:
                raise BenchError(f"{prompt} is required. Pass it as a flag.")
            return default
        hint = f" [{default}]" if default else ""
        while True:
            answer = input(f"{prompt}{hint}: ").strip() or default
            if answer or not required:
                return answer

    def _confirm(self, prompt: str, value: bool) -> bool:
        if value or not sys.stdin.isatty():
            return value
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")

    def _git_config(self, key: str) -> str:
        try:
            return run_command(["git", "config", "--get", key]).stdout.decode().strip()
        except CommandError:
            return ""
