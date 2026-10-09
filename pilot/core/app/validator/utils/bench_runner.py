from __future__ import annotations

import json
import subprocess
import typing

from pilot.exceptions import AppValidationError


def run_in_bench(bench_python: str, script: str, payload: typing.Any = None) -> typing.Any:
    """Executes a Python script in the bench's Python interpreter subprocess."""
    try:
        res = subprocess.run(
            [bench_python, "-c", script],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        raise AppValidationError(
            f"Failed to execute bench Python interpreter '{bench_python}': {exc}"
        ) from exc

    if res.returncode != 0:
        error_msg = res.stderr.strip() or f"Process exited with code {res.returncode}"
        raise AppValidationError(f"Bench Python execution failed under '{bench_python}': {error_msg}")

    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise AppValidationError(
            f"Bench Python returned malformed output under '{bench_python}': {res.stdout.strip()}"
        ) from exc


_BATCH_SYNTAX_SCRIPT = """
import ast, json, sys

try:
    files = json.load(sys.stdin)
except Exception as e:
    sys.stderr.write(f"Failed to read input files: {e}")
    sys.exit(1)

errors = {}
for file_path in files:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            ast.parse(f.read(), filename=file_path)
    except SyntaxError as exc:
        errors[file_path] = f"line {exc.lineno}: {exc.msg}"
    except (OSError, UnicodeDecodeError):
        pass

print(json.dumps(errors))
"""


def batch_check_syntax(bench_python: str, files: list[str]) -> dict[str, str]:
    """Runs ast.parse over a list of files in a single batch subprocess."""
    return run_in_bench(bench_python, _BATCH_SYNTAX_SCRIPT, files)
