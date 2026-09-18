from __future__ import annotations

import ast
import typing

from pilot._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
from pilot.core.app.validator.base import bench_table, module_path, read_pyproject
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App

_EXAMPLE_SPECIFIER = ">=16.0.0,<17.0.0"


class DependencyDeclarationsCheck:
    """Ensure hooks and pyproject.toml have sane dependency requirements."""

    def run(self, app: "App") -> None:
        if app.module_name == "frappe":
            return  # the framework itself has nothing to declare a dependency on

        declared = self.get_frappe_dependencies(app)
        if "frappe" not in declared:
            raise AppValidationError(
                f"'{app.config.name}' must declare the frappe versions it supports in pyproject.toml.\n"
                "Add:\n"
                "  [tool.bench.frappe-dependencies]\n"
                f'  frappe = "{_EXAMPLE_SPECIFIER}"'
            )
        self._check_version_specifiers(app, declared)

        # hooks.py's required_apps never lists frappe itself (it's implicit),
        # while pyproject.toml always does - exclude it before comparing.
        missing = sorted(set(self.get_hooks_required_apps(app)) - (set(declared) - {"frappe"}))
        if missing:
            raise AppValidationError(
                f"'{app.config.name}' requires {missing} in hooks.py, but pyproject.toml's "
                "[tool.bench.frappe-dependencies] doesn't declare them.\n"
                f'Add one entry per app, e.g. {missing[0]} = "{_EXAMPLE_SPECIFIER}"'
            )

    def get_hooks_required_apps(self, app: "App") -> list[str]:
        """Parse hooks.py (guaranteed present by RepoStructureCheck) for required_apps."""
        hooks_path = module_path(app) / "hooks.py"
        tree = ast.parse(hooks_path.read_text(), filename=str(hooks_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "required_apps"
                        and isinstance(node.value, (ast.List, ast.Tuple))
                    ):
                        return [
                            elt.value.rsplit("/", 1)[-1]
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
        return []

    def get_frappe_dependencies(self, app: "App") -> dict[str, str]:
        """The `[tool.bench.frappe-dependencies]` table as {app: version specifier}."""
        pyproject_data = read_pyproject(app)
        if pyproject_data is None:
            raise AppValidationError(
                f"'{app.config.name}' has no pyproject.toml, so it can't declare the frappe "
                "versions it supports. Scaffold one with 'bench new-app'."
            )

        declared = bench_table(app).get("frappe-dependencies", {})
        if not isinstance(declared, dict):
            raise AppValidationError(
                f"'{app.config.name}' has an invalid [tool.bench.frappe-dependencies] in "
                f"pyproject.toml: expected a table of versions, got {type(declared).__name__}.\n"
                f'Use one entry per app, e.g. frappe = "{_EXAMPLE_SPECIFIER}"'
            )
        return {name: str(specifier) for name, specifier in declared.items()}

    @staticmethod
    def _check_version_specifiers(app: "App", declared: dict[str, str]) -> None:
        """Every app needs a real range: an unpinned or unreadable one can't be resolved."""
        for name, specifier in declared.items():
            if not specifier.strip():
                raise AppValidationError(
                    f"'{app.config.name}' declares '{name}' with no version in pyproject.toml's "
                    "[tool.bench.frappe-dependencies].\n"
                    f'Pin the versions it supports, e.g. {name} = "{_EXAMPLE_SPECIFIER}"'
                )
            try:
                SpecifierSet(specifier)
            except InvalidSpecifier as exc:
                raise AppValidationError(
                    f"'{app.config.name}' declares an invalid version for '{name}' in "
                    f"pyproject.toml's [tool.bench.frappe-dependencies]: {specifier!r} ({exc}).\n"
                    f'Use comma-separated PEP 440 ranges, e.g. {name} = "{_EXAMPLE_SPECIFIER}"'
                ) from exc
