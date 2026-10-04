from __future__ import annotations

import json
import subprocess
import sys
import typing
from pathlib import Path

from pilot.core.app.validator.base import python_files
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
        bench = getattr(app, "bench", None)
        if bench and hasattr(bench, "env_path") and bench.env_path:
            bench_python = Path(bench.env_path) / "bin" / "python"
            if bench_python.exists():
                return str(bench_python)
        return sys.executable

    @classmethod
    def _syntax_errors(cls, app: "App", files: list[str]) -> dict[str, str]:
        """Runs ast.parse across all files in a single batch subprocess using the bench Python runner."""
        python_bin = cls._get_python_bin(app)
        script = """
import ast
import json
import sys

try:
    files = json.load(sys.stdin)
except Exception as e:
    sys.stderr.write(f"Failed to read input files: {e}")
    sys.exit(1)

errors = {}
for file_path in files:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read(), filename=file_path)
    except SyntaxError as exc:
        errors[file_path] = f"line {exc.lineno}: {exc.msg}"
    except OSError:
        pass

print(json.dumps(errors))
"""
        cmd = [python_bin, "-c", script]
        try:
            res = subprocess.run(
                cmd,
                input=json.dumps(files),
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            raise AppValidationError(
                f"Failed to execute syntax validator with '{python_bin}': {exc}"
            ) from exc

        if res.returncode != 0:
            error_msg = res.stderr.strip() or f"Process exited with code {res.returncode}"
            raise AppValidationError(
                f"Syntax validator failed under '{python_bin}': {error_msg}"
            )

        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise AppValidationError(
                f"Syntax validator returned malformed output under '{python_bin}': {res.stdout.strip()}"
            ) from exc
