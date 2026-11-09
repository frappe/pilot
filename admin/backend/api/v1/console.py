from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal, TypedDict

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites.shared import malformed_body
from pilot.core.bench import Bench
from pilot.core.site.console.python_completer import PythonCompleter
from pilot.exceptions import LowMemoryError


class ConsoleCompletion(TypedDict):
    label: str
    type: Literal["namespace", "class", "function", "keyword", "property", "variable"]
    detail: str


class PythonDocstring(TypedDict):
    docstring: str


console_bp = Blueprint("console", __name__)

# The helper outlives requests, so the Admin process keeps one per bench it serves.
_completers: dict[Path, PythonCompleter] = {}
_completers_lock = threading.Lock()


@console_bp.post("/completions")
def get_python_completions():
    """Python completions from Jedi at a 1-based `line` and 0-based `column` of `code`. `controllers` maps doctypes to their controller class paths."""
    cursor, response = _parse_cursor_request(request.get_json(silent=True))
    return response or jsonify(_get_completer().get_completions(*cursor))


@console_bp.post("/docstring")
def get_python_docstring():
    """Docstring of the completion called `name` at the same position a completions request used."""
    data = request.get_json(silent=True)
    cursor, response = _parse_cursor_request(data)
    if response:
        return response
    if not isinstance(data.get("name"), str):
        return error_response("invalid_fields", "Send the completion `name`.", 422)
    return jsonify(PythonDocstring(docstring=_get_completer().get_docstring(*cursor, data["name"])))


@console_bp.errorhandler(LowMemoryError)
def _low_memory_error(error: LowMemoryError):
    return error_response("low_memory", str(error), 503)


def _parse_cursor_request(data):
    """`(code, line, column, controllers)` from the body, or an error response."""
    if not isinstance(data, dict):
        return None, malformed_body()
    cursor = (data.get("code"), data.get("line"), data.get("column"), data.get("controllers", {}))
    if not all(isinstance(value, kind) for value, kind in zip(cursor, (str, int, int, dict), strict=True)):
        return None, error_response(
            "invalid_fields", "Send `code`, `line`, `column`, and `controllers`.", 422
        )
    return cursor, None


def _get_completer() -> PythonCompleter:
    root = Path(current_app.config["BENCH_ROOT"])
    with _completers_lock:
        if root not in _completers:
            _completers[root] = PythonCompleter(Bench(root))
        return _completers[root]
