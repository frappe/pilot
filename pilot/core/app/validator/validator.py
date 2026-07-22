from __future__ import annotations

import typing

from pilot.core.app.validator.dependency_declarations import DependencyDeclarationsCheck
from pilot.core.app.validator.imports import ImportCheck
from pilot.core.app.validator.repo_structure import RepoStructureCheck
from pilot.core.app.validator.syntax import SyntaxCheck

if typing.TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.app.validator.base import ValidationCheck


class Validator:
    """Runs pre-install checks against a cloned app."""

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
