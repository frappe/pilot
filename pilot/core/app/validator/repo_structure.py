from __future__ import annotations

import typing

from pilot.core.app.validator.base import module_path, read_pyproject
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class RepoStructureCheck:
    """Verifies a cloned app has the files pilot expects before installing it."""

    def run(self, app: "App") -> None:
        """Raise when the repository is not an installable Frappe app."""
        if not (app.path / "pyproject.toml").exists():
            raise AppValidationError(
                f"'{app.config.name}' has no pyproject.toml, so it isn't an installable frappe app. "
                "Scaffold one with 'bench new-app'."
            )

        read_pyproject(app)  # rejects broken TOML before any check reads it

        path = module_path(app)
        if not path.is_dir():
            raise AppValidationError(
                f"'{app.config.name}' has no '{app.module_name}' package directory. A frappe app's "
                f"python package must be named after the app, so rename it to '{app.module_name}'."
            )
        if not (path / "hooks.py").exists():
            raise AppValidationError(
                f"'{app.config.name}' is missing {app.module_name}/hooks.py, which every frappe app "
                "needs. Copy one from an app scaffolded by 'bench new-app'."
            )
