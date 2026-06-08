from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.toml_writer import bench_config_to_toml
from ..validators import first_error, validate_email, validate_port, validate_worker_count

settings_bp = Blueprint("settings", __name__)

# Fields whose change requires a bench process restart
_RESTART_KEYS = {
    ("bench", "python"),
    ("bench", "http_port"),
    ("bench", "socketio_port"),
    ("mariadb", "host"),
    ("mariadb", "port"),
    ("mariadb", "admin_user"),
    ("mariadb", "socket_path"),
    ("redis", "cache_port"),
    ("redis", "queue_port"),
    ("redis", "socketio_port"),
    ("workers", "default"),
    ("workers", "short"),
    ("workers", "long"),
    ("production", "process_manager"),
}


def _needs_restart(old: dict, new: dict) -> bool:
    for section, key in _RESTART_KEYS:
        if old.get(section, {}).get(key) != new.get(section, {}).get(key):
            return True
    return False


def _supervisor_conf(bench_root: Path) -> Path | None:
    p = bench_root / "config" / "supervisor" / "supervisord.conf"
    return p if p.exists() else None


def _bench_name(bench_root: Path) -> str:
    try:
        with open(bench_root / "bench.toml", "rb") as f:
            return tomllib.load(f).get("bench", {}).get("name", "bench")
    except Exception:
        return "bench"


def _supervisorctl(conf: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["supervisorctl", "-c", str(conf), *args],
        capture_output=True, text=True, timeout=30,
    )


def _non_admin_programs(conf: Path, bench_name: str) -> list[str]:
    result = _supervisorctl(conf, "status", f"{bench_name}:*")
    programs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        full_name = line.split()[0]
        if not full_name.endswith("-admin"):
            programs.append(full_name)
    return programs


def _config_snapshot(config: BenchConfig) -> dict:
    return {
        "bench": {
            "python": config.python_version,
            "http_port": config.http_port,
            "socketio_port": config.socketio_port,
        },
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
        },
        "redis": {
            "cache_port": config.redis.cache_port,
            "queue_port": config.redis.queue_port,
            "socketio_port": config.redis.socketio_port,
        },
        "workers": {
            "default": config.workers.default_count,
            "short": config.workers.short_count,
            "long": config.workers.long_count,
        },
        "production": {
            "process_manager": config.production.process_manager,
        },
    }


@settings_bp.route("/")
def get_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "bench": {
            "name": config.name,
            "python": config.python_version,
            "http_port": config.http_port,
            "socketio_port": config.socketio_port,
        },
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
            "version": config.mariadb.version or "",
        },
        "redis": {
            "cache_port": config.redis.cache_port,
            "queue_port": config.redis.queue_port,
            "socketio_port": config.redis.socketio_port,
            "version": config.redis.version or "",
        },
        "workers": {
            "default": config.workers.default_count,
            "short": config.workers.short_count,
            "long": config.workers.long_count,
        },
        "nginx": {
            "http_port": config.nginx.http_port,
            "https_port": config.nginx.https_port,
            "config_dir": str(config.nginx.config_dir),
            "worker_processes": config.nginx.worker_processes,
            "client_max_body_size": config.nginx.client_max_body_size,
        },
        "letsencrypt": {
            "email": config.letsencrypt.email,
            "webroot_path": str(config.letsencrypt.webroot_path),
        },
        "production": {
            "process_manager": config.production.process_manager,
            "nginx": config.production.nginx,
        },
    })


@settings_bp.route("/", methods=["PATCH"])
def update_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    old_snapshot = _config_snapshot(config)

    bench_data = data.get("bench", {})
    if "python" in bench_data:
        config.python_version = str(bench_data["python"])
    if "http_port" in bench_data:
        config.http_port = int(bench_data["http_port"])
    if "socketio_port" in bench_data:
        config.socketio_port = int(bench_data["socketio_port"])

    mariadb_data = data.get("mariadb", {})
    if mariadb_data:
        m = config.mariadb
        m.host = str(mariadb_data.get("host", m.host))
        m.port = int(mariadb_data.get("port", m.port))
        m.admin_user = str(mariadb_data.get("admin_user", m.admin_user))
        m.socket_path = str(mariadb_data.get("socket_path", m.socket_path))
        version = str(mariadb_data.get("version", "")).strip()
        m.version = version or None

    redis_data = data.get("redis", {})
    if redis_data:
        r = config.redis
        r.cache_port = int(redis_data.get("cache_port", r.cache_port))
        r.queue_port = int(redis_data.get("queue_port", r.queue_port))
        r.socketio_port = int(redis_data.get("socketio_port", r.socketio_port))
        version = str(redis_data.get("version", "")).strip()
        r.version = version or None

    workers_data = data.get("workers", {})
    if workers_data:
        w = config.workers
        w.default_count = int(workers_data.get("default", w.default_count))
        w.short_count = int(workers_data.get("short", w.short_count))
        w.long_count = int(workers_data.get("long", w.long_count))

    nginx_data = data.get("nginx", {})
    if nginx_data:
        n = config.nginx
        n.http_port = int(nginx_data.get("http_port", n.http_port))
        n.https_port = int(nginx_data.get("https_port", n.https_port))
        if "config_dir" in nginx_data:
            n.config_dir = Path(str(nginx_data["config_dir"]))
        n.worker_processes = str(nginx_data.get("worker_processes", n.worker_processes))
        n.client_max_body_size = str(nginx_data.get("client_max_body_size", n.client_max_body_size))

    le_data = data.get("letsencrypt", {})
    if le_data:
        le = config.letsencrypt
        le.email = str(le_data.get("email", le.email))
        if "webroot_path" in le_data:
            le.webroot_path = Path(str(le_data["webroot_path"]))

    production_data = data.get("production", {})
    if production_data:
        p = config.production
        if "process_manager" in production_data:
            pm = str(production_data["process_manager"])
            if pm not in ("none", "supervisor", "systemd"):
                return jsonify({"ok": False, "error": "process_manager must be none, supervisor, or systemd"}), 400
            p.process_manager = pm
        p.nginx = bool(production_data.get("nginx", p.nginx))

    err = first_error(
        validate_port(config.http_port, "HTTP Port"),
        validate_port(config.socketio_port, "SocketIO Port"),
        validate_port(config.mariadb.port, "MariaDB Port"),
        validate_port(config.redis.cache_port, "Redis Cache Port"),
        validate_port(config.redis.queue_port, "Redis Queue Port"),
        validate_port(config.redis.socketio_port, "Redis SocketIO Port"),
        validate_port(config.nginx.http_port, "Nginx HTTP Port"),
        validate_port(config.nginx.https_port, "Nginx HTTPS Port"),
        validate_worker_count(config.workers.default_count, "Default workers"),
        validate_worker_count(config.workers.short_count, "Short workers"),
        validate_worker_count(config.workers.long_count, "Long workers"),
        validate_email(config.letsencrypt.email),
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    try:
        config.validate()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    try:
        toml_str = bench_config_to_toml(config)
        (bench_root / "bench.toml").write_text(toml_str)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to write config: {e}"}), 500

    restarted = False
    restart_error = None
    new_snapshot = _config_snapshot(config)

    if _needs_restart(old_snapshot, new_snapshot):
        conf = _supervisor_conf(bench_root)
        if conf is not None:
            bench_name = _bench_name(bench_root)
            programs = _non_admin_programs(conf, bench_name)
            if programs:
                result = _supervisorctl(conf, "restart", *programs)
                if result.returncode == 0:
                    restarted = True
                else:
                    restart_error = result.stderr or result.stdout

    return jsonify({"ok": True, "restarted": restarted, "restart_error": restart_error})
