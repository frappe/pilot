from __future__ import annotations

import ast
import typing

import tomllib

from pilot.core.app_validator.base import module_path
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class DependencyDeclarationsCheck:
    """Ensure hooks and pyproject.toml have sane dependency requirements."""

    def run(self, app: "App") -> None:
        required_apps_from_hooks = self._get_hooks_required_apps(app)
        declared_required_apps_in_pyproject = self._get_pyproject_required_apps(app)

        missing = set(required_apps_from_hooks) - set(declared_required_apps_in_pyproject)
        if missing:
            raise AppValidationError(
                f"'{app.config.name}' requires {sorted(missing)} in hooks.py but they're "
                "missing from pyproject.toml's [tool.bench.frappe-dependencies]."
            )

    def _get_hooks_required_apps(self, app: "App") -> list[str]:
        """Parse hooks.py (guaranteed present by RepoStructureCheck) for required_apps."""
        hooks_path = module_path(app) / "hooks.py"
        tree = ast.parse(hooks_path.read_text(), filename=str(hooks_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "required_apps":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            return [
                                # Entries may be "org/app" (any org, not just
                                # "frappe/") or a bare app name — pyproject.toml
                                # keys are always just the bare app name.
                                elt.value.rsplit("/", 1)[-1]
                                for elt in node.value.elts
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            ]
        return []

    def _get_pyproject_required_apps(self, app: "App") -> list[str]:
        """Parse pyproject.toml (guaranteed present by RepoStructureCheck) for required apps."""
        pyproject_path = app.path / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)

        declared_dependencies = (
            pyproject_data.get("tool", {}).get("bench", {}).get("frappe-dependencies", [])
        )

        if isinstance(declared_dependencies, dict):
            return list(declared_dependencies.keys())
        if isinstance(declared_dependencies, list):
            return declared_dependencies

        raise AppValidationError(
            f"'{app.config.name}' has an invalid [tool.bench.frappe-dependencies] in "
            f"pyproject.toml: expected a table or list, got {type(declared_dependencies).__name__}."
        )
