"""Per-check breakdown of App.validate(), the gate every install and update passes."""

from __future__ import annotations

from benchmark.fixtures import Fixture
from benchmark.harness import Step, StepCase
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


def _setup(fixture: Fixture) -> None:
    fixture.app = fixture.bench().app("app1")


CASES = [
    StepCase(
        name="validator.steps",
        group="validator",
        description="Each App.validate() check timed separately (subprocesses included)",
        steps=(
            Step("repo_structure", lambda fixture: RepoStructureCheck().run(fixture.app)),
            Step("version_specifiers", lambda fixture: VersionSpecifiersCheck().run(fixture.app)),
            Step("symlinks", lambda fixture: SymlinkCheck().run(fixture.app)),
            Step("syntax", lambda fixture: SyntaxCheck().run(fixture.app)),
            Step("hooks", lambda fixture: HooksCheck().run(fixture.app)),
            Step("fixtures", lambda fixture: FixturesCheck().run(fixture.app)),
            Step(
                "dependency_declarations",
                lambda fixture: DependencyDeclarationsCheck().run(fixture.app),
            ),
            Step(
                "frappe_compatibility",
                lambda fixture: FrappeCompatibilityCheck().run(fixture.app),
            ),
            Step(
                "dependency_resolution",
                lambda fixture: DependencyResolutionCheck().run(fixture.app),
            ),
            Step("imports", lambda fixture: ImportCheck().run(fixture.app)),
        ),
        setup=_setup,
        iterations=5,
        warmup=1,
        profile=True,
    ),
]
