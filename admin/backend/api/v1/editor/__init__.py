from __future__ import annotations

from functools import wraps
from pathlib import Path

from flask import Blueprint, request

from admin.backend.api.requests import ApiRequest
from admin.backend.api.responses import error_response
from admin.backend.core.editor.scope import EditorScope
from admin.backend.core.editor.workspace import (
    BinaryFileError,
    EditorPathError,
    ExcludedPathError,
    HiddenPathError,
)
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_app_name

editor_bp = Blueprint("editor", __name__)


def with_workspace(view):
    """Resolve the request's scope to a workspace: `?app=` on the editor routes, the site name on
    `/sites/<name>/editor`. Each takes an optional `?base=` folder below its root."""

    @wraps(view)
    def wrapper(*args, name: str | None = None, **kwargs):
        """Answer 404 unless the code editor is on, then call the view."""
        bench_root = ApiRequest.get_bench_root()
        config = _read_config(bench_root)
        if config is None or not config.enable_code_editor:
            return error_response("editor_disabled", "Code editor is turned off. Turn it on in Settings.", 403)
        bench = Bench(config, bench_root)
        scope = get_site_scope(bench, name) if name else get_app_scope(bench)
        if scope is None:
            return error_response("editor_scope_not_found", "Unknown or missing app or site.", 404)
        return view(scope.get_workspace(), *args, **kwargs)

    return require_scope(lambda view_args: view_args.get("name"), scopes=("site_editor",))(wrapper)


def get_app_scope(bench: Bench) -> EditorScope | None:
    """The app scope `?app=` names, or None."""
    name = (request.args.get("app") or "").strip()
    if validate_app_name(name) is not None:
        return None
    try:
        return EditorScope.for_app(bench, name, request.args.get("base", ""))
    except BenchError:
        return None


def get_site_scope(bench: Bench, name: str) -> EditorScope | None:
    """The site scope `/sites/<name>/editor` names, or None."""
    if not site_exists(bench.path, name):
        return None
    return EditorScope.for_site(bench, name, request.args.get("base", ""))


def is_code_editor_enabled(bench_root: Path) -> bool:
    """Whether the code editor is on."""
    config = _read_config(bench_root)
    return bool(config and config.enable_code_editor)


def _read_config(bench_root: Path):
    """Parse bench.toml without validation: the editor reads a couple of fields on a hot path."""
    from pilot.config import BenchConfig

    try:
        return BenchConfig.read(bench_root, validate=False)
    except Exception:
        return None


def query_flag(name: str) -> bool:
    """Whether a query flag is 1."""
    return request.args.get(name) == "1"


def json_body() -> dict:
    """The JSON body, or an empty dict."""
    return request.get_json(silent=True) or {}


@editor_bp.errorhandler(EditorPathError)
def _path_error(_):
    """Answer 400 for a path outside the app."""
    return error_response("invalid_path", "Path escapes the app directory.", 400)


@editor_bp.errorhandler(HiddenPathError)
def _hidden_error(_):
    """Answer 403 for a hidden path."""
    return error_response(
        "hidden_path", "Dot files and folders cannot be opened or changed in the editor.", 403
    )


@editor_bp.errorhandler(ExcludedPathError)
def _excluded_error(_):
    """Answer 403 for a path the scope keeps out of the editor."""
    return error_response("excluded_path", "This path cannot be opened or changed in the editor.", 403)


@editor_bp.errorhandler(BinaryFileError)
def _binary_error(_):
    """Answer 415 for a binary file."""
    return error_response("binary_file", "File is not text.", 415)


@editor_bp.errorhandler(FileNotFoundError)
def _missing_error(_):
    """Answer 404 for a missing file."""
    return error_response("not_found", "File not found.", 404)


@editor_bp.errorhandler(IsADirectoryError)
@editor_bp.errorhandler(NotADirectoryError)
def _kind_error(_):
    """Answer 400 for a file where a folder is needed, or the other way round."""
    return error_response(
        "wrong_kind", "Path is a folder where a file is needed, or the other way round.", 400
    )


@editor_bp.errorhandler(FileExistsError)
def _exists_error(_):
    """Answer 409 for a path that exists."""
    return error_response("already_exists", "Path already exists.", 409)


# Route modules attach their handlers to editor_bp; import last to avoid a cycle.
from admin.backend.api.v1.editor.routes import (  # noqa: E402,F401
    files,
    git,
    search,
    state,
)
