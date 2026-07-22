from __future__ import annotations

import tomllib
import typing

from pilot.core.app.validator.base import module_path
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class RepoStructureCheck:
    """Verifies a cloned app has the files pilot expects before installing it."""

    def run(self, app: "App") -> None:
        if not (app.path / "pyproject.toml").exists():
            raise AppValidationError(f"'{app.config.name}' has no pyproject.toml.")

        try:
            with open((app.path / "pyproject.toml"), "rb") as f:
                tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise AppValidationError(f"'{app.config.name}' has an invalid pyproject.toml: {exc}") from exc

        path = module_path(app)
        if not path.is_dir():
            raise AppValidationError(f"'{app.config.name}' has no '{app.module_name}' package directory.")
        if not (path / "hooks.py").exists():
            raise AppValidationError(f"'{app.config.name}' is missing {app.module_name}/hooks.py.")
