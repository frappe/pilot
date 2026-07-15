from __future__ import annotations

import ast
import typing
from pathlib import Path

from pilot.core.app_validator.base import python_files
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class SyntaxCheck:
    """Ast-parses every Python file in the app, rejecting it on any SyntaxError."""

    def run(self, app: "App") -> None:
        broken = [
            f"{path.relative_to(app.path)}: {error}"
            for path in python_files(app)
            for error in self._syntax_errors(path)
        ]
        if broken:
            raise AppValidationError(
                f"'{app.config.name}' has Python syntax errors:\n"
                + "\n".join(f"  {b}" for b in broken)
            )

    @staticmethod
    def _syntax_errors(path: Path) -> list[str]:
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            return [f"line {exc.lineno}: {exc.msg}"]
        except OSError:
            return []
        return []
