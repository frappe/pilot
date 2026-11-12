from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, Command

if TYPE_CHECKING:
    from pilot.core.app import App


@dataclass(kw_only=True)
class GetAppCommand(Command):
    name: ClassVar[str] = "get-app"
    help: ClassVar[str] = "Clone and install an app."

    repo: Annotated[str, Arg(help="Git repository URL.")]
    branch: Annotated[str, Arg(help="Git branch to checkout (default: the repository's default branch).")] = (
        ""
    )
    commit: Annotated[
        str, Arg(help="Git commit hash to checkout (default: the latest commit on the branch).")
    ] = ""
    skip_assets: Annotated[bool, Arg(help="Skip building assets")] = False
    install_dependencies: Annotated[bool, Arg(help="Install app dependencies")] = False

    def __post_init__(self) -> None:
        from pilot.core.app import App

        self.app = App.from_repo(self.bench, self.repo, self.branch)
        self.installed_dependencies: list[App] = []

    def run(self) -> None:
        """Get the app into the bench."""
        result = self.app.install(
            install_dependencies=self.install_dependencies,
            skip_assets=self.skip_assets,
            on_progress=self.report,
            commit=self.commit,
        )
        self.app = result.app
        self.installed_dependencies = result.installed_dependencies
        # Running workers cannot import an app added after they started.
        if not result.already_installed:
            self.bench.reload_workers()
