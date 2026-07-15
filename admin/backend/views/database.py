from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api_contract import error_response
from pilot.exceptions import DatabaseProcessNotActiveError

from ..readers.bench_reader import BenchReader
from ..readers.database_reader import DatabaseReader

database_bp = Blueprint("database", __name__)


def _get_database_reader(bench_root) -> DatabaseReader:
    config = BenchReader(bench_root).config()
    return DatabaseReader(config.mariadb)


def _get_mariadb_manager(bench_root):
    from pilot.managers.mariadb_manager import MariaDBManager

    config = BenchReader(bench_root).config()
    return MariaDBManager(config.mariadb)


@database_bp.route("/binlogs")
def binlogs():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        reader = _get_database_reader(bench_root)
        binary_logs = reader.list_binary_logs()
    except Exception:
        return error_response("binlogs_unavailable", "Could not read binary logs.", 500)

    return jsonify([{"log_name": bl.log_name, "file_size": bl.file_size} for bl in binary_logs])


@database_bp.route("/binlogs/<log_name>")
def binlog_detail(log_name: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        limit = int(request.args.get("limit", 200))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        limit, offset = 200, 0

    try:
        reader = _get_database_reader(bench_root)
        events = reader.read_binary_log_events(log_name, limit=limit, offset=offset)
    except Exception:
        return error_response("binlog_unavailable", "Could not read the binary log.", 500)

    return jsonify(
        {
            "log_name": log_name,
            "limit": limit,
            "offset": offset,
            "events": [
                {
                    "log_name": e.log_name,
                    "pos": e.pos,
                    "event_type": e.event_type,
                    "server_id": e.server_id,
                    "end_log_pos": e.end_log_pos,
                    "info": e.info,
                }
                for e in events
            ],
        }
    )


@database_bp.route("/processlist")
def processlist():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        reader = _get_database_reader(bench_root)
        rows = reader.read_processlist()
    except Exception:
        return error_response(
            "database_processes_unavailable",
            "Could not read database processes.",
            500,
        )
    return jsonify(
        [
            {
                "id": r["Id"],
                "user": r["User"],
                "host": r["Host"],
                "db": r["db"] or "",
                "command": r["Command"],
                "time": r["Time"],
                "state": r["State"] or "",
                "info": r["Info"] or "",
            }
            for r in rows
            if r["Info"] != "SHOW FULL PROCESSLIST"
        ]
    )


@database_bp.route("/processlist/<int:process_id>", methods=["DELETE"])
def kill_process(process_id: int):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        _get_mariadb_manager(bench_root).kill_process(process_id)
    except DatabaseProcessNotActiveError:
        return error_response(
            "database_process_not_active",
            f"Database process {process_id} is no longer active.",
            409,
        )
    except Exception:
        return error_response(
            "database_process_kill_failed",
            "Could not stop the database process.",
            500,
        )
    return jsonify({"ok": True})


@database_bp.route("/slow-queries")
def slow_queries():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = min(limit, 500)

    try:
        reader = _get_database_reader(bench_root)
        queries = reader.read_slow_queries(limit=limit)
    except Exception:
        return error_response(
            "slow_queries_unavailable",
            "Could not read slow queries.",
            500,
        )

    return jsonify(
        [
            {
                "timestamp": q.timestamp.isoformat(),
                "query_time": q.query_time,
                "lock_time": q.lock_time,
                "rows_examined": q.rows_examined,
                "rows_sent": q.rows_sent,
                "user_host": q.user_host,
                "sql": q.sql,
            }
            for q in queries
        ]
    )


# ── SQL playground ────────────────────────────────────────────────────────────


@database_bp.route("/playground/sites")
def playground_sites():
    import json

    bench_root: Path = current_app.config["BENCH_ROOT"]
    sites_path = bench_root / "sites"
    if not sites_path.is_dir():
        return jsonify([])
    site_dirs = sorted(
        d for d in sites_path.iterdir()
        if d.is_dir() and (d / "site_config.json").exists()
    )
    sites = []
    for d in site_dirs:
        try:
            cfg = json.loads((d / "site_config.json").read_text())
        except (OSError, ValueError):
            cfg = {}
        sites.append({"name": d.name, "db_type": cfg.get("db_type", "mariadb")})
    return jsonify(sites)


@database_bp.route("/playground/schema")
def playground_schema():
    bench_root: Path = current_app.config["BENCH_ROOT"]
    site = request.args.get("site", "")
    if not site:
        return error_response("invalid_site", "Site is required.", 422)
    try:
        from pilot.core.database import make_site_database
        return jsonify(make_site_database(bench_root, site).get_schema())
    except FileNotFoundError:
        return error_response("site_not_found", "Site was not found.", 404)
    except Exception:
        return error_response("schema_unavailable", "Could not read database schema.", 500)


@database_bp.route("/playground/execute", methods=["POST"])
def playground_execute():
    bench_root: Path = current_app.config["BENCH_ROOT"]
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    site = data.get("site", "")
    query = data.get("query", "")
    read_only = data.get("read_only", True)
    if not isinstance(site, str):
        return error_response("invalid_site", "Site must be a string.", 422)
    if not isinstance(query, str):
        return error_response("invalid_query", "Query must be a string.", 422)
    if not isinstance(read_only, bool):
        return error_response("invalid_read_only", "read_only must be a boolean.", 422)
    site = site.strip()
    query = query.strip()
    if not site:
        return error_response("invalid_site", "Site is required.", 422)
    if not query:
        return error_response("invalid_query", "Query is required.", 422)
    try:
        from pilot.core.database import make_site_database
        db = make_site_database(bench_root, site)
        result = db.execute(query, read_only=read_only)
        return jsonify({
            "columns": result.columns,
            "rows": result.rows,
            "row_count": len(result.rows),
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "affected_rows": result.affected_rows,
        })
    except FileNotFoundError:
        return error_response("site_not_found", "Site was not found.", 404)
    except Exception:
        return error_response("query_failed", "Could not execute query.", 500)
