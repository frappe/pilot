from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

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
    except Exception as error:
        return jsonify({"error": str(error)}), 500

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
    except Exception as error:
        return jsonify({"error": str(error)}), 500

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
    except Exception as error:
        return jsonify({"error": str(error)}), 500
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
    except Exception:
        return jsonify({"ok": False, "error": f"Process with process ID {process_id} has finished"}), 500
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
    except Exception as error:
        return jsonify({"error": str(error)}), 500

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
