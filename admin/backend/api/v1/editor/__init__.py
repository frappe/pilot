from __future__ import annotations

from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, request

from admin.backend.api.responses import error_response
from admin.core.editor.workspace import (
    BinaryFileError,
    EditorPathError,
    EditorWorkspace,
)
from pilot.core.bench import Bench
from pilot.internal.validators import validate_app_name

editor_bp = Blueprint("editor", __name__)


def with_workspace(view):
    """Resolve ?app=<name> to a workspace; 404 for unknown or missing apps."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        bench_root = Path(current_app.config["BENCH_ROOT"])
        if not is_developer_mode_enabled(bench_root):
            return error_response("editor_disabled", "Code editor is disabled. Enable Developer Mode in Settings.", 403)
        name = (request.args.get("app") or "").strip()
        if validate_app_name(name) is not None:
            return error_response("editor_app_not_found", "Unknown or missing app.", 404)
        try:
            root = Bench(bench_root).app(name).path
        except Exception:
            return error_response("editor_app_not_found", "Unknown or missing app.", 404)
        return view(EditorWorkspace(root), *args, **kwargs)

    return wrapper


def is_developer_mode_enabled(bench_root: Path) -> bool:
    from pilot.config import BenchConfig

    try:
        return BenchConfig.read(bench_root).allow_developer_mode
    except Exception:
        return False


def query_flag(name: str) -> bool:
    return request.args.get(name) == "1"


def json_body() -> dict:
    return request.get_json(silent=True) or {}


@editor_bp.errorhandler(EditorPathError)
def _path_error(_):
    return error_response("invalid_path", "Path escapes the app directory.", 400)


@editor_bp.errorhandler(BinaryFileError)
def _binary_error(_):
    return error_response("binary_file", "File is not text.", 415)


@editor_bp.errorhandler(FileNotFoundError)
def _missing_error(_):
    return error_response("not_found", "File not found.", 404)


@editor_bp.errorhandler(FileExistsError)
def _exists_error(_):
    return error_response("already_exists", "Path already exists.", 409)


# Route modules attach their handlers to editor_bp; import last to avoid a cycle.
from admin.backend.api.v1.editor.routes import (  # noqa: E402,F401
    events,
    files,
    git,
    search,
    state,
)
