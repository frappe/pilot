from __future__ import annotations

import ast
import json
import subprocess
import typing
from pathlib import Path

from pilot.core.app.validator.base import python_files
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class SyntaxCheck:
    """AST-parses every Python file in the app using the bench's Python environment."""

    def run(self, app: "App") -> None:
        files = [str(p) for p in python_files(app)]
        if not files:
            return

        python_bin = self._get_python_bin(app)
        syntax_errors = self._check_syntax_in_env(files, python_bin)

        broken = [
            f"{Path(path).relative_to(app.path)}: {error}"
            for path, error in syntax_errors.items()
        ]

        if broken:
            raise AppValidationError(
                f"'{app.config.name}' has Python syntax errors:\n"
                + "\n".join(f"  {b}" for b in broken)
            )

    @staticmethod
    def _get_python_bin(app: "App") -> str:
        """Returns the path to the bench's Python binary, falling back to python3."""
        if hasattr(app, "bench") and app.bench and hasattr(app.bench, "path"):
            bench_python = Path(app.bench.path) / "env" / "bin" / "python"
            if bench_python.exists():
                return str(bench_python)
        return "python3"

    @staticmethod
    def _check_syntax_in_env(files: list[str], python_bin: str) -> dict[str, str]:
        """Runs ast.parse across all files in a single subprocess using the target bench Python runner."""
        script = """
import ast
import json
import sys

errors = {}
for file_path in sys.argv[1:]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read(), filename=file_path)
    except SyntaxError as exc:
        errors[file_path] = f"line {exc.lineno}: {exc.msg}"
    except OSError:
        pass

print(json.dumps(errors))
"""
        cmd = [python_bin, "-c", script] + files
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode == 0 and res.stdout.strip():
            try:
                return json.loads(res.stdout)
            except json.JSONDecodeError:
                pass

        return {}
