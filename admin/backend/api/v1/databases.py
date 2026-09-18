from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.api.v1.benches.support import guard_bench_management
from pilot.core.bench import Bench
from pilot.core.database.configurations import (
    DatabaseConfigurationChange,
    DatabaseConfigurations,
)
from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.exceptions import DatabaseError, TaskConflictError
from pilot.tasks.restart_database import RestartDatabaseTask
from pilot.tasks.set_innodb_buffer_pool_size import SetInnoDBBufferPoolSizeTask
from pilot.tasks.set_mariadb_configuration import SetMariaDBConfigurationTask
from pilot.tasks.set_max_database_connections import SetMaxDatabaseConnectionsTask
from pilot.tasks.set_performance_schema import SetPerformanceSchemaTask

database_bp = Blueprint("database", __name__)
_DATABASE_RESOURCE_KEY = "database-server"


def _bench() -> Bench:
    return Bench(Path(current_app.config["BENCH_ROOT"]))


def _quick_actions() -> DatabaseQuickActions:
    return DatabaseQuickActions(_bench().config)


def _configurations() -> DatabaseConfigurations:
    return DatabaseConfigurations(_bench().config)


def _queue_configuration_change(
    bench: Bench,
    change: DatabaseConfigurationChange,
    idempotency_key: str | None,
) -> str:
    action = change["action"]
    if action == "configuration":
        return SetMariaDBConfigurationTask.queue(
            bench,
            variable=change["name"],
            value_json=json.dumps(change["value"], separators=(",", ":")),
            idempotency_key=idempotency_key,
            resource_key=_DATABASE_RESOURCE_KEY,
        )
    if action == "performance_schema":
        enabled = change["value"]
        if type(enabled) is not bool:
            raise ValueError("Performance Schema must be either enabled or disabled.")
        return SetPerformanceSchemaTask.queue(
            bench,
            state="enabled" if enabled else "disabled",
            idempotency_key=idempotency_key,
            resource_key=_DATABASE_RESOURCE_KEY,
        )
    if action == "innodb_buffer_pool_size":
        size_mb = change["value"]
        if type(size_mb) is not int:
            raise ValueError("InnoDB Buffer Pool size must be a whole number.")
        return SetInnoDBBufferPoolSizeTask.queue(
            bench,
            size_mb=size_mb,
            idempotency_key=idempotency_key,
            resource_key=_DATABASE_RESOURCE_KEY,
        )
    if action == "max_connections":
        max_connections = change["value"]
        if type(max_connections) is not int:
            raise ValueError("Maximum connections must be a whole number.")
        return SetMaxDatabaseConnectionsTask.queue(
            bench,
            max_connections=max_connections,
            idempotency_key=idempotency_key,
            resource_key=_DATABASE_RESOURCE_KEY,
        )
    raise ValueError(f"Unknown database configuration action '{action}'.")


@database_bp.get("/sites")
def list_query_sites() -> ResponseReturnValue:
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
        sites.append(
            {
                "name": d.name,
                "db_type": config.get("db_type", "mariadb"),
                "db_name": config.get("db_name", ""),
            }
        )
    return jsonify(sites)


@database_bp.get("/schema")
def get_schema() -> ResponseReturnValue:
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
def execute_query() -> ResponseReturnValue:
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


@database_bp.get("/quick-actions")
def get_quick_actions():
    try:
        return jsonify(_quick_actions().capabilities())
    except DatabaseError as exc:
        return error_response("quick_actions_unavailable", str(exc), 422)
    except Exception:
        return error_response("quick_actions_unavailable", "Could not read quick actions.", 500)


@database_bp.get("/configurations")
def get_database_configurations():
    try:
        return jsonify(_configurations().snapshot())
    except DatabaseError as exc:
        return error_response("database_configurations_unavailable", str(exc), 422)
    except Exception:
        return error_response(
            "database_configurations_unavailable",
            "Could not read database configurations.",
            500,
        )


@database_bp.post("/configurations/<variable>")
def set_database_configuration(variable: str):
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"value"}:
        return error_response(
            "invalid_database_configuration",
            "The request must contain exactly one value field.",
            422,
        )

    try:
        configurations = _configurations()
        change = configurations.prepare_change(variable, data["value"])
        if not change["changed"]:
            return error_response(
                "database_configuration_unchanged",
                f"MariaDB variable '{variable}' already has the requested value.",
                409,
            )
        bench = _bench()
        task_id = _queue_configuration_change(
            bench,
            change,
            request.headers.get("Idempotency-Key"),
        )
        return accepted_task_response(bench.path, task_id)
    except ValueError as exc:
        return error_response("invalid_database_configuration", str(exc), 422)
    except (DatabaseError, TaskConflictError) as exc:
        return error_response("database_configuration_unavailable", str(exc), 409)
    except Exception:
        return error_response(
            "database_configuration_failed",
            "Could not queue the database configuration change.",
            500,
        )


@database_bp.post("/quick-actions/restart")
def restart_database():
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    try:
        _quick_actions().require_restart()
        bench = _bench()
        task_id = RestartDatabaseTask.queue(
            bench,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=_DATABASE_RESOURCE_KEY,
        )
        return accepted_task_response(bench.path, task_id)
    except (DatabaseError, TaskConflictError) as exc:
        return error_response("database_action_unavailable", str(exc), 409)
    except ValueError as exc:
        return error_response("invalid_database_action", str(exc), 422)
    except Exception:
        return error_response("database_action_failed", "Could not queue the database restart.", 500)


@database_bp.post("/quick-actions/performance-schema")
def set_performance_schema():
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    enabled = data.get("enabled") if isinstance(data, dict) else None
    if type(enabled) is not bool:
        return error_response("invalid_enabled", "enabled must be a boolean.", 422)

    try:
        actions = _quick_actions()
        capability = actions.require_performance_schema()
        if capability["enabled"] is enabled:
            state = "enabled" if enabled else "disabled"
            return error_response(
                "database_action_unchanged",
                f"Performance Schema is already {state}.",
                409,
            )
        bench = _bench()
        task_id = SetPerformanceSchemaTask.queue(
            bench,
            state="enabled" if enabled else "disabled",
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=_DATABASE_RESOURCE_KEY,
        )
        return accepted_task_response(bench.path, task_id)
    except (DatabaseError, TaskConflictError) as exc:
        return error_response("database_action_unavailable", str(exc), 409)
    except ValueError as exc:
        return error_response("invalid_database_action", str(exc), 422)
    except Exception:
        return error_response(
            "database_action_failed",
            "Could not queue the Performance Schema change.",
            500,
        )


@database_bp.post("/quick-actions/innodb-buffer-pool-size")
def set_innodb_buffer_pool_size():
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    size_mb = data.get("size_mb") if isinstance(data, dict) else None
    if type(size_mb) is not int:
        return error_response("invalid_size_mb", "size_mb must be a whole number.", 422)

    try:
        actions = _quick_actions()
        capability = actions.require_innodb_buffer_pool_size(size_mb)
        if capability["current_mb"] == size_mb:
            return error_response(
                "database_action_unchanged",
                f"InnoDB Buffer Pool size is already {size_mb} MB.",
                409,
            )
        bench = _bench()
        task_id = SetInnoDBBufferPoolSizeTask.queue(
            bench,
            size_mb=size_mb,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=_DATABASE_RESOURCE_KEY,
        )
        return accepted_task_response(bench.path, task_id)
    except (DatabaseError, TaskConflictError) as exc:
        return error_response("database_action_unavailable", str(exc), 409)
    except ValueError as exc:
        return error_response("invalid_size_mb", str(exc), 422)
    except Exception:
        return error_response(
            "database_action_failed",
            "Could not queue the InnoDB Buffer Pool size change.",
            500,
        )


@database_bp.post("/quick-actions/max-connections")
def set_max_connections():
    forbidden = guard_bench_management()
    if forbidden is not None:
        return forbidden

    data = request.get_json(silent=True)
    max_connections = data.get("max_connections") if isinstance(data, dict) else None
    if type(max_connections) is not int:
        return error_response(
            "invalid_max_connections",
            "max_connections must be a whole number.",
            422,
        )

    try:
        actions = _quick_actions()
        capability = actions.require_max_connections(max_connections)
        if capability["current"] == max_connections:
            return error_response(
                "database_action_unchanged",
                f"Max DB Connections is already {max_connections}.",
                409,
            )
        bench = _bench()
        task_id = SetMaxDatabaseConnectionsTask.queue(
            bench,
            max_connections=max_connections,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=_DATABASE_RESOURCE_KEY,
        )
        return accepted_task_response(bench.path, task_id)
    except (DatabaseError, TaskConflictError) as exc:
        return error_response("database_action_unavailable", str(exc), 409)
    except ValueError as exc:
        return error_response("invalid_max_connections", str(exc), 422)
    except Exception:
        return error_response(
            "database_action_failed",
            "Could not queue the Max DB Connections change.",
            500,
        )


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


@database_bp.get("/performance-report")
def get_performance_report() -> ResponseReturnValue:
    provider = _provider()
    sections = {
        "time_consuming_queries": provider.get_time_consuming_queries,
        "full_table_scan_queries": provider.get_full_table_scan_queries,
        "unused_indexes": provider.get_unused_indexes,
        "redundant_indexes": provider.get_redundant_indexes,
    }
    section = sections.get(request.args.get("report_type", ""))
    if section is None:
        return error_response("invalid_report_type", f"Expected one of {', '.join(sections)}.", 422)
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return error_response("invalid_page", "limit and offset must be whole numbers.", 422)
    try:
        return jsonify(section(request.args.get("site", ""), limit, offset))
    except DatabaseError as exc:
        return error_response("performance_report_unavailable", str(exc), 422)
    except Exception:
        return error_response(
            "performance_report_unavailable",
            "Could not read the database performance report.",
            500,
        )


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
