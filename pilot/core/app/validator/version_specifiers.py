from __future__ import annotations

import typing

from pilot._vendor.packaging.requirements import InvalidRequirement, Requirement
from pilot._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
from pilot.core.app.validator.base import read_pyproject
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class VersionSpecifiersCheck:
    """Reject pyproject.toml version specifiers that uv/pip would refuse.

    Catches malformed PEP 440 specifier sets like ``>=20.19 <21`` (missing
    comma) and malformed requirement strings before they reach install.
    """

    def run(self, app: "App") -> None:
        """Raise when a version specifier is invalid."""
        data = read_pyproject(app)
        if data is None:
            return  # an app without pyproject.toml has no specifiers to check
        project = data.get("project", {})
        if not isinstance(project, dict):
            return  # RepoStructureCheck owns pyproject shape; nothing to read here

        self._check_requires_python(app, project)
        self._check_dependencies(app, project.get("dependencies", []), "dependencies")
        extras = project.get("optional-dependencies", {})
        if isinstance(extras, dict):
            for group, deps in extras.items():
                if isinstance(deps, list):
                    self._check_dependencies(app, deps, f"optional-dependencies.{group}")

    def _check_requires_python(self, app: "App", project: dict) -> None:
        """Raise when requires-python is invalid."""
        value = project.get("requires-python")
        if not isinstance(value, str) or not value.strip():
            return
        try:
            SpecifierSet(value)
        except InvalidSpecifier as exc:
            raise AppValidationError(
                f"'{app.config.name}' has an invalid requires-python in pyproject.toml: {value!r} ({exc}).\n"
                'Use comma-separated PEP 440 ranges, e.g. requires-python = ">=3.10,<3.14"'
            ) from exc

    def _check_dependencies(self, app: "App", deps: object, where: str) -> None:
        """Raise when a dependency line is invalid."""
        if not isinstance(deps, list):
            return
        for entry in deps:
            if not isinstance(entry, str) or not entry.strip():
                continue
            try:
                Requirement(entry)
            except InvalidRequirement as exc:
                raise AppValidationError(
                    f"'{app.config.name}' has an invalid dependency in pyproject.toml "
                    f"[{where}]: {entry!r} ({exc}).\n"
                    'Use comma-separated PEP 440 ranges, e.g. "frappe>=16.0.0,<17.0.0"'
                ) from exc
