from __future__ import annotations

import typing

from pilot.core.app_validator.dependency_declarations import DependencyDeclarationsCheck
from pilot.core.app_validator.imports import ImportCheck
from pilot.core.app_validator.repo_structure import RepoStructureCheck
from pilot.core.app_validator.syntax import SyntaxCheck

if typing.TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.app_validator.base import ValidationCheck


class Validator:
    """Runs a cloned app through a series of checks before it's installed
    into the bench env, so a broken app is rejected up front instead of
    after `pip install` has already touched the environment."""

    def __init__(self, app: "App", checks: list["ValidationCheck"] | None = None) -> None:
        self.app = app
        self.checks = checks or [
            RepoStructureCheck(),
            SyntaxCheck(),
            DependencyDeclarationsCheck(),
            ImportCheck(),
        ]

    def validate(self) -> None:
        for check in self.checks:
            check.run(self.app)
