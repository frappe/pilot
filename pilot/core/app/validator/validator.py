from __future__ import annotations

import typing

from pilot.core.app.validator.dependency_declarations import DependencyDeclarationsCheck
from pilot.core.app.validator.dependency_resolution import DependencyResolutionCheck
from pilot.core.app.validator.fixtures import FixturesCheck
from pilot.core.app.validator.frappe_compatibility import FrappeCompatibilityCheck
from pilot.core.app.validator.hooks import HooksCheck
from pilot.core.app.validator.imports import ImportCheck
from pilot.core.app.validator.repo_structure import RepoStructureCheck
from pilot.core.app.validator.symlinks import SymlinkCheck
from pilot.core.app.validator.syntax import SyntaxCheck
from pilot.core.app.validator.version_specifiers import VersionSpecifiersCheck

if typing.TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.app.validator.base import ValidationCheck


class Validator:
    """Runs validation checks against an app."""

    def __init__(self, app: "App", checks: list["ValidationCheck"] | None = None) -> None:
        self.app = app
        self.checks = checks or _all_checks()

    def validate(self) -> None:
        """Run every check."""
        for check in self.checks:
            check.run(self.app)


def _all_checks() -> list["ValidationCheck"]:
    """Every check, on every path. An app that has moved to a new revision is
    held to the same standard as one being installed: the revision can have
    dropped a pyproject.toml or a declaration just as easily as a hook.
    """
    return [
        RepoStructureCheck(),
        VersionSpecifiersCheck(),
        SymlinkCheck(),
        SyntaxCheck(),
        HooksCheck(),
        FixturesCheck(),
        DependencyDeclarationsCheck(),
        FrappeCompatibilityCheck(),
        DependencyResolutionCheck(),
        ImportCheck(),
    ]
