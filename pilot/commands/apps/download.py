from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command

if TYPE_CHECKING:
    from pilot.core.app import App


@dataclass(kw_only=True)
class GetAppCommand(Command):
    name: ClassVar[str] = "get-app"
    help: ClassVar[str] = "Clone and install an app."

    repo: Annotated[str, Arg(help="Git repository URL.")]
    branch: Annotated[str, Arg(help="Git branch to checkout.")] = ""
    install_dependencies: Annotated[bool, Arg(help="Install app dependencies")] = False
    skip_validations: Annotated[bool, Arg(help="Skip running app validations")] = False

    def __post_init__(self) -> None:
        from pilot.core.app import App

        self.app = App.from_repo(self.bench, self.repo, self.branch or "main")
        self.installed_dependencies: list[App] = []

    def run(self) -> None:
        result = self.app.install(
            install_dependencies=self.install_dependencies,
            skip_validations=self.skip_validations,
            on_progress=self.print,
        )
        self.app = result.app
        self.installed_dependencies = result.installed_dependencies
