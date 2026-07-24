from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.benches.support import guard_bench_management
from pilot.exceptions import DatabaseError

database_bp = Blueprint("database", __name__)


@database_bp.get("/sites")
def list_query_sites():
    bench_root: Path = current_app.config["BENCH_ROOT"]
    sites_path = bench_root / "sites"
    if not sites_path.is_dir():
        return jsonify([])
    site_dirs = sorted(d for d in sites_path.iterdir() if d.is_dir() and (d / "site_config.json").exists())
    sites = []
    for d in site_dirs:
        try:
            config = json.loads((d / "site_config.json").read_text())
        except (OSError, ValueError):
            config = {}
        sites.append({"name": d.name, "db_type": config.get("db_type", "mariadb")})
    return jsonify(sites)


@database_bp.get("/schema")
def get_schema():
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


@database_bp.post("/queries")
def execute_query():
    bench_root: Path = current_app.config["BENCH_ROOT"]
    data = request.get_json(silent=True)

    query_data, response = _query_request(data)
    if response is not None:
        return response

    try:
        from pilot.core.database import make_site_database

        db = make_site_database(bench_root, query_data["site"])
        result = db.execute(query_data["query"], read_only=query_data["read_only"])
        return jsonify(
            {
                "columns": result.columns,
                "rows": result.rows,
                "row_count": len(result.rows),
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
                "affected_rows": result.affected_rows,
            }
        )
    except FileNotFoundError:
        return error_response("site_not_found", "Site was not found.", 404)
    except Exception:
        return error_response("query_failed", "Could not execute query.", 500)


def _provider():
    from admin.backend.providers.database import DatabaseDiagnosticsProvider

    return DatabaseDiagnosticsProvider(current_app.config["BENCH_ROOT"])


@database_bp.get("/diagnostics")
def get_diagnostics():
    try:
        return jsonify(_provider().get_diagnostics())
    except DatabaseError as exc:
        return error_response("diagnostics_unavailable", str(exc), 422)
    except Exception:
        return error_response("diagnostics_unavailable", "Could not read database diagnostics.", 500)


@database_bp.get("/processlist")
def get_process_list():
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden
    try:
        return jsonify(_provider().get_process_list(request.args.get("site", "")))
    except DatabaseError as exc:
        return error_response("processlist_unavailable", str(exc), 422)
    except Exception:
        return error_response("processlist_unavailable", "Could not read the database process list.", 500)


@database_bp.post("/processlist/kill")
def kill_process():
    # A killed connection can belong to any bench sharing this server.
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    process_id = data.get("process_id") if isinstance(data, dict) else None
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return error_response("invalid_process_id", "process_id must be a positive integer.", 422)
    try:
        _provider().kill_process(process_id)
        return jsonify({"status": "ok"})
    except DatabaseError as exc:
        return error_response("kill_failed", str(exc), 422)
    except Exception:
        return error_response("kill_failed", "Could not kill the database process.", 500)


@database_bp.get("/lockwaits")
def get_lock_wait_rows():
    try:
        return jsonify(_provider().get_lock_wait_rows(request.args.get("site", "")))
    except DatabaseError as exc:
        return error_response("lockwaits_unavailable", str(exc), 422)
    except Exception:
        return error_response("lockwaits_unavailable", "Could not read database lock waits.", 500)


@database_bp.get("/size")
def get_database_size():
    try:
        return jsonify(_provider().get_database_size(request.args.get("site", "")))
    except DatabaseError as exc:
        return error_response("size_unavailable", str(exc), 422)
    except Exception:
        return error_response("size_unavailable", "Could not read the database size.", 500)


@database_bp.get("/table-sizes")
def get_table_sizes():
    try:
        return jsonify(_provider().get_table_sizes(request.args.get("site", "")))
    except DatabaseError as exc:
        return error_response("table_sizes_unavailable", str(exc), 422)
    except Exception:
        return error_response("table_sizes_unavailable", "Could not read table sizes.", 500)


@database_bp.get("/binlogs")
def get_binlogs():
    try:
        return jsonify(_provider().get_binlog_files())
    except DatabaseError as exc:
        return error_response("binlogs_unavailable", str(exc), 422)
    except Exception:
        return error_response("binlogs_unavailable", "Could not list binary logs.", 500)


@database_bp.post("/binlogs/purge")
def purge_binlogs():
    # Binlogs are server-wide state shared by every bench on this host.
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    up_to = data.get("up_to", "") if isinstance(data, dict) else ""
    if not isinstance(up_to, str) or not up_to.strip():
        return error_response("invalid_up_to", "up_to is required.", 422)
    try:
        _provider().purge_binlogs(up_to.strip())
        return jsonify({"status": "ok"})
    except DatabaseError as exc:
        return error_response("purge_failed", str(exc), 422)
    except Exception:
        return error_response("purge_failed", "Could not purge binary logs.", 500)


def _query_request(data):
    if not isinstance(data, dict):
        return {}, error_response("malformed_request", "Expected a JSON object.", 400)

    site = data.get("site", "")
    query = data.get("query", "")
    read_only = data.get("read_only", True)

    if not isinstance(site, str):
        return {}, error_response("invalid_site", "Site must be a string.", 422)
    if not isinstance(query, str):
        return {}, error_response("invalid_query", "Query must be a string.", 422)
    if not isinstance(read_only, bool):
        return {}, error_response("invalid_read_only", "read_only must be a boolean.", 422)

    site = site.strip()
    query = query.strip()
    if not site:
        return {}, error_response("invalid_site", "Site is required.", 422)
    if not query:
        return {}, error_response("invalid_query", "Query is required.", 422)

    return {"site": site, "query": query, "read_only": read_only}, None
