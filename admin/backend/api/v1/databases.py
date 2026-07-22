from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response

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
