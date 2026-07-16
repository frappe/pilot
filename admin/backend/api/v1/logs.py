from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from admin.backend.api.responses import error_response
from admin.backend.providers.logs import LogProvider

logs_bp = Blueprint("logs", __name__)

_MAX_LINES = 5000


@logs_bp.get("")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        log_files = LogProvider(bench_root).get_all()
    except Exception:
        return error_response("logs_unavailable", "Could not read logs.", 500)

    return jsonify([
        {
            "filename": lf.filename,
            "size_bytes": lf.size_bytes,
            "last_modified": lf.last_modified.isoformat(),
            "process_name": lf.process_name,
            "line_count": lf.line_count,
        }
        for lf in log_files
    ])


@logs_bp.route("/<filename>")
def viewer(filename: str):
    bench_root = current_app.config["BENCH_ROOT"]
    search = request.args.get("search", "").strip()

    try:
        lines_param = int(request.args.get("lines", 200))
    except ValueError:
        lines_param = 200
    lines_param = min(lines_param, _MAX_LINES)

    try:
        provider = LogProvider(bench_root)
        lines = provider.tail_file(filename, lines_param)
        if search:
            search_lower = search.lower()
            lines = [line for line in lines if search_lower in line.lower()]
    except ValueError:
        return error_response("invalid_log", "Invalid log filename.", 422)
    except Exception:
        return error_response("log_unavailable", "Could not read the log.", 500)

    return jsonify({
        "filename": filename,
        "lines": lines,
        "lines_count": lines_param,
        "search": search,
    })


@logs_bp.get("/<filename>/content")
def download_log(filename: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        provider = LogProvider(bench_root)
        log_path = provider.get_file_path(filename)
    except ValueError:
        return error_response("invalid_log", "Invalid log filename.", 422)

    if not log_path.exists():
        return error_response("log_not_found", "Log file was not found.", 404)

    return Response(
        log_path.read_bytes(),
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@logs_bp.get("/<filename>/events")
def stream_log(filename: str):
    bench_root = current_app.config["BENCH_ROOT"]
    provider = LogProvider(bench_root)

    def generate():
        try:
            for line in provider.follow_file(filename):
                yield f"data: {json.dumps({'line': line})}\n\n"
        except ValueError as error:
            yield f"data: {json.dumps({'error': str(error)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")
