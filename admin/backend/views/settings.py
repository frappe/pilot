from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.toml_writer import bench_config_to_toml
from bench_cli.platform import is_linux

from ..validators import first_error, validate_email, validate_port, validate_worker_count

settings_bp = Blueprint("settings", __name__)

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
    return any(old.get(s, {}).get(k) != new.get(s, {}).get(k) for s, k in _RESTART_KEYS)


def _restart_trigger_values(config: BenchConfig) -> dict:
    return {
        "bench": {"python": config.python_version, "http_port": config.http_port, "socketio_port": config.socketio_port},
        "mariadb": {"host": config.mariadb.host, "port": config.mariadb.port, "admin_user": config.mariadb.admin_user, "socket_path": config.mariadb.socket_path},
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port, "socketio_port": config.redis.socketio_port},
        "workers": {"default": config.workers.default_count, "short": config.workers.short_count, "long": config.workers.long_count},
        "production": {"process_manager": config.production.process_manager},
    }


def _zfs_quota_values(config: BenchConfig) -> dict:
    v = config.volume
    return {
        "benches_quota": v.benches.quota,
        "benches_reservation": v.benches.reservation,
        "mariadb_quota": v.mariadb.quota,
        "mariadb_reservation": v.mariadb.reservation,
    }


def _patch_config(config: BenchConfig, data: dict) -> str | None:
    if b := data.get("bench"):
        if "http_port" in b:
            config.http_port = int(b["http_port"])
        if "socketio_port" in b:
            config.socketio_port = int(b["socketio_port"])

    if m := data.get("mariadb"):
        config.mariadb.host = str(m.get("host", config.mariadb.host))
        config.mariadb.port = int(m.get("port", config.mariadb.port))
        config.mariadb.admin_user = str(m.get("admin_user", config.mariadb.admin_user))
        config.mariadb.socket_path = str(m.get("socket_path", config.mariadb.socket_path))

    if r := data.get("redis"):
        config.redis.cache_port = int(r.get("cache_port", config.redis.cache_port))
        config.redis.queue_port = int(r.get("queue_port", config.redis.queue_port))
        config.redis.socketio_port = int(r.get("socketio_port", config.redis.socketio_port))

    if w := data.get("workers"):
        config.workers.default_count = int(w.get("default", config.workers.default_count))
        config.workers.short_count = int(w.get("short", config.workers.short_count))
        config.workers.long_count = int(w.get("long", config.workers.long_count))

    if n := data.get("nginx"):
        config.nginx.http_port = int(n.get("http_port", config.nginx.http_port))
        config.nginx.https_port = int(n.get("https_port", config.nginx.https_port))
        if "config_dir" in n:
            config.nginx.config_dir = Path(str(n["config_dir"]))
        config.nginx.worker_processes = str(n.get("worker_processes", config.nginx.worker_processes))
        config.nginx.client_max_body_size = str(n.get("client_max_body_size", config.nginx.client_max_body_size))

    if le := data.get("letsencrypt"):
        config.letsencrypt.email = str(le.get("email", config.letsencrypt.email))
        if "webroot_path" in le:
            config.letsencrypt.webroot_path = Path(str(le["webroot_path"]))

    if v := data.get("volume"):
        vol = config.volume
        vol.benches.reservation = str(v.get("benches_reservation", vol.benches.reservation))
        vol.benches.quota = str(v.get("benches_quota", vol.benches.quota))
        vol.mariadb.reservation = str(v.get("mariadb_reservation", vol.mariadb.reservation))
        vol.mariadb.quota = str(v.get("mariadb_quota", vol.mariadb.quota))
        vol.snapshots.enabled = bool(v.get("snapshots_enabled", vol.snapshots.enabled))

    if p := data.get("production"):
        if "process_manager" in p:
            pm = str(p["process_manager"])
            if pm not in ("none", "supervisor", "systemd"):
                return "process_manager must be none, supervisor, or systemd"
            config.production.process_manager = pm
        config.production.nginx = bool(p.get("nginx", config.production.nginx))

    return first_error(
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


# ── ZFS application ───────────────────────────────────────────────────────────


def _validate_volume_quota(config: BenchConfig, old: dict) -> str | None:
    from bench_cli.managers.volume_manager import VolumeManager

    vol = config.volume
    if not vol.enabled:
        return None
    manager = VolumeManager(vol)
    new = _zfs_quota_values(config)
    for dataset, key in [(vol.benches_dataset, "benches_quota"), (vol.mariadb_dataset, "mariadb_quota")]:
        if new[key] != old[key]:
            if err := manager.validate_quota(dataset, new[key]):
                return err
    return None


def _apply_dataset_zfs(manager, dataset: str, q_key: str, r_key: str, old: dict, new: dict) -> str | None:
    from bench_cli.exceptions import VolumeError

    if not manager.dataset_exists(dataset):
        return None
    try:
        if new[q_key] != old[q_key]:
            manager.set_quota(dataset, new[q_key])
        if new[r_key] != old[r_key]:
            manager.set_reservation(dataset, new[r_key])
    except VolumeError as e:
        return str(e)
    return None


def _apply_volume_zfs(config: BenchConfig, old: dict) -> str | None:
    from bench_cli.managers.volume_manager import VolumeManager

    vol = config.volume
    if not vol.enabled:
        return None
    manager = VolumeManager(vol)
    new = _zfs_quota_values(config)
    return _apply_dataset_zfs(manager, vol.benches_dataset, "benches_quota", "benches_reservation", old, new) or _apply_dataset_zfs(
        manager, vol.mariadb_dataset, "mariadb_quota", "mariadb_reservation", old, new
    )


# ── Process restart ───────────────────────────────────────────────────────────


def _non_admin_supervisor_programs(conf: Path, bench_name: str) -> list[str]:
    result = subprocess.run(
        ["supervisorctl", "-c", str(conf), "status", f"{bench_name}:*"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip() and not line.split()[0].endswith("-admin")]


def _regenerate_configs(bench_root: Path, config: BenchConfig) -> None:
    from bench_cli.core.bench import Bench
    from bench_cli.managers.process_manager import ProcessManagerFactory
    from bench_cli.managers.redis_manager import RedisManager

    bench = Bench(config, bench_root)
    RedisManager(config.redis, bench).generate_configs()
    ProcessManagerFactory.create(bench).generate_config()


def _restart_supervisor(manager, bench_name: str) -> tuple[bool, str | None]:
    if not manager.is_alive():
        return False, None
    subprocess.run([*manager._supervisorctl(), "reread"], capture_output=True, timeout=10)
    subprocess.run([*manager._supervisorctl(), "update"], capture_output=True, timeout=10)
    programs = _non_admin_supervisor_programs(manager.supervisor_conf_path, bench_name)
    if not programs:
        return False, None
    result = subprocess.run([*manager._supervisorctl(), "restart", *programs], capture_output=True, text=True, timeout=30)
    return (result.returncode == 0), (result.stderr or result.stdout if result.returncode != 0 else None)


def _restart_systemd(manager) -> tuple[bool, str | None]:
    if not manager.is_running():
        return False, None
    env = manager._systemctl_env()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, env=env, timeout=10)
    non_admin_units = [manager._unit_name(pd.name) for pd in manager._prod_process_definitions() if pd.name != "admin"]
    if not non_admin_units:
        return False, None
    result = subprocess.run([*manager._systemctl(), "restart", *non_admin_units], capture_output=True, text=True, timeout=60, env=env)
    return (result.returncode == 0), (result.stderr or result.stdout if result.returncode != 0 else None)


def _do_restart(bench_root: Path, config: BenchConfig) -> tuple[bool, str | None]:
    from bench_cli.core.bench import Bench
    from bench_cli.managers.process_manager import ProcessManagerFactory
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager

    bench = Bench(config, bench_root)
    manager = ProcessManagerFactory.detect_running(bench)
    if isinstance(manager, SupervisorProcessManager):
        return _restart_supervisor(manager, config.name)
    if isinstance(manager, SystemdProcessManager):
        return _restart_systemd(manager)
    return False, None


# ── Response builder ──────────────────────────────────────────────────────────


def _build_settings_response(config: BenchConfig) -> dict:
    m, r, n, le, v = config.mariadb, config.redis, config.nginx, config.letsencrypt, config.volume
    return {
        "is_linux": is_linux(),
        "bench": {"name": config.name, "python": config.python_version, "http_port": config.http_port, "socketio_port": config.socketio_port},
        "mariadb": {"host": m.host, "port": m.port, "admin_user": m.admin_user, "socket_path": m.socket_path, "version": m.version or ""},
        "redis": {"cache_port": r.cache_port, "queue_port": r.queue_port, "socketio_port": r.socketio_port, "version": r.version or ""},
        "workers": {"default": config.workers.default_count, "short": config.workers.short_count, "long": config.workers.long_count},
        "nginx": {"http_port": n.http_port, "https_port": n.https_port, "config_dir": str(n.config_dir), "worker_processes": n.worker_processes, "client_max_body_size": n.client_max_body_size},
        "letsencrypt": {"email": le.email, "webroot_path": str(le.webroot_path)},
        "production": {"process_manager": config.production.process_manager, "nginx": config.production.nginx},
        "volume": {
            "enabled": v.enabled,
            "pool": v.pool,
            "device": v.device,
            "benches_reservation": v.benches.reservation,
            "benches_quota": v.benches.quota,
            "mariadb_reservation": v.mariadb.reservation,
            "mariadb_quota": v.mariadb.quota,
            "mariadb_data_dir": v.mariadb.data_dir,
            "snapshots_enabled": v.snapshots.enabled,
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@settings_bp.route("/")
def get_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(_build_settings_response(config))


@settings_bp.route("/", methods=["PATCH"])
def update_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    old_restart = _restart_trigger_values(config)
    old_zfs = _zfs_quota_values(config)

    if err := _patch_config(config, data):
        return jsonify({"ok": False, "error": err}), 400

    try:
        config.validate()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if vol_err := _validate_volume_quota(config, old_zfs):
        return jsonify({"ok": False, "error": vol_err}), 400

    try:
        (bench_root / "bench.toml").write_text(bench_config_to_toml(config))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to write config: {e}"}), 500

    zfs_error = _apply_volume_zfs(config, old_zfs)

    restarted, restart_error = False, None
    if _needs_restart(old_restart, _restart_trigger_values(config)):
        try:
            _regenerate_configs(bench_root, config)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to regenerate configs: {e}"}), 500
        restarted, restart_error = _do_restart(bench_root, config)

    return jsonify({"ok": True, "restarted": restarted, "restart_error": restart_error, "zfs_error": zfs_error})
