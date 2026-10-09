from __future__ import annotations

import typing
from pathlib import Path

from pilot.core.app.validator.base import get_bench_python, python_files
from pilot.core.app.validator.utils.bench_runner import batch_check_syntax
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class SyntaxCheck:
    """AST-parses every Python file in the app using the bench's Python environment, rejecting it on any SyntaxError."""

    def run(self, app: "App") -> None:
        files = [str(p) for p in python_files(app)]
        if not files:
            return

        syntax_errors = self._syntax_errors(app, files)

        broken = [
            f"{Path(path).relative_to(app.path)}: {error}"
            for path, error in syntax_errors.items()
        ]

        if broken:
            raise AppValidationError(
                f"'{app.config.name}' has Python syntax errors:\n"
                + "\n".join(f"  {b}" for b in broken)
            )

    @classmethod
    def _get_python_bin(cls, app: "App") -> str:
        """Returns the path to the bench's Python binary, falling back to the current Pilot Python executable."""
        return get_bench_python(app)

    @classmethod
    def _syntax_errors(cls, app: "App", files: list[str]) -> dict[str, str]:
        """Runs ast.parse across all files in a single batch subprocess using the bench Python runner."""
        python_bin = cls._get_python_bin(app)
        return batch_check_syntax(python_bin, files)
