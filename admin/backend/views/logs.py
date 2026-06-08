from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from ..readers.log_reader import LogReader

logs_bp = Blueprint("logs", __name__)

_MAX_LINES = 5000


@logs_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        log_files = LogReader(bench_root).list_logs()
    except Exception as error:
        return jsonify({"error": str(error)}), 500

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
        reader = LogReader(bench_root)
        lines = reader.read_tail(filename, lines_param)
        if search:
            search_lower = search.lower()
            lines = [l for l in lines if search_lower in l.lower()]
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({
        "filename": filename,
        "lines": lines,
        "lines_count": lines_param,
        "search": search,
    })


@logs_bp.route("/<filename>/download")
def download_log(filename: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        reader = LogReader(bench_root)
        log_path = reader.file_path(filename)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if not log_path.exists():
        return jsonify({"error": f"Log file not found: {filename}"}), 404

    return Response(
        log_path.read_bytes(),
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@logs_bp.route("/<filename>/stream")
def stream_log(filename: str):
    bench_root = current_app.config["BENCH_ROOT"]
    reader = LogReader(bench_root)

    def generate():
        try:
            for line in reader.stream_tail(filename):
                yield f"data: {line}\n\n"
        except ValueError as error:
            yield f"data: ERROR: {error}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")
