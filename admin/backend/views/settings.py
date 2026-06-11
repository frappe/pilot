from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.toml_writer import bench_config_to_toml
from bench_cli.managers.volume_manager import VolumeManager
from bench_cli.platform import is_linux

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
    return any(old.get(section, {}).get(key) != new.get(section, {}).get(key) for section, key in _RESTART_KEYS)


def _restart_trigger_values(config: BenchConfig) -> dict:
    return {
        "bench": {"python": config.python_version, "http_port": config.http_port, "socketio_port": config.socketio_port},
        "mariadb": {"host": config.mariadb.host, "port": config.mariadb.port, "admin_user": config.mariadb.admin_user, "socket_path": config.mariadb.socket_path},
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port, "socketio_port": config.redis.socketio_port},
        "workers": {"default": config.workers.default_count, "short": config.workers.short_count, "long": config.workers.long_count},
        "production": {"process_manager": config.production.process_manager},
    }


# ── Config patching ───────────────────────────────────────────────────────────


class ConfigPatcher:
    def __init__(self, config: BenchConfig, data: dict) -> None:
        self.config = config
        self.data = data

    def apply(self) -> str | None:
        self._apply_bench()
        self._apply_mariadb()
        self._apply_redis()
        self._apply_workers()
        self._apply_nginx()
        self._apply_letsencrypt()
        self._apply_volume()
        if error := self._apply_production():
            return error
        try:
            self.config.validate()
        except Exception as error:
            return str(error)
        return None

    def _apply_bench(self) -> None:
        bench = self.data.get("bench") or {}
        if "http_port" in bench:
            self.config.http_port = int(bench["http_port"])
        if "socketio_port" in bench:
            self.config.socketio_port = int(bench["socketio_port"])
        if "default_branch" in bench:
            self.config.default_branch = str(bench["default_branch"]).strip()

    def _apply_mariadb(self) -> None:
        mariadb = self.data.get("mariadb") or {}
        if not mariadb:
            return
        mariadb_config = self.config.mariadb
        mariadb_config.host = str(mariadb.get("host", mariadb_config.host))
        mariadb_config.port = int(mariadb.get("port", mariadb_config.port))
        mariadb_config.admin_user = str(mariadb.get("admin_user", mariadb_config.admin_user))
        mariadb_config.socket_path = str(mariadb.get("socket_path", mariadb_config.socket_path))

    def _apply_redis(self) -> None:
        redis = self.data.get("redis") or {}
        if not redis:
            return
        redis_config = self.config.redis
        redis_config.cache_port = int(redis.get("cache_port", redis_config.cache_port))
        redis_config.queue_port = int(redis.get("queue_port", redis_config.queue_port))
        redis_config.socketio_port = int(redis.get("socketio_port", redis_config.socketio_port))

    def _apply_workers(self) -> None:
        workers = self.data.get("workers") or {}
        if not workers:
            return
        workers_config = self.config.workers
        workers_config.default_count = int(workers.get("default", workers_config.default_count))
        workers_config.short_count = int(workers.get("short", workers_config.short_count))
        workers_config.long_count = int(workers.get("long", workers_config.long_count))

    def _apply_nginx(self) -> None:
        nginx = self.data.get("nginx") or {}
        if not nginx:
            return
        nginx_config = self.config.nginx
        nginx_config.http_port = int(nginx.get("http_port", nginx_config.http_port))
        nginx_config.https_port = int(nginx.get("https_port", nginx_config.https_port))
        if "config_dir" in nginx:
            nginx_config.config_dir = Path(str(nginx["config_dir"]))
        nginx_config.worker_processes = str(nginx.get("worker_processes", nginx_config.worker_processes))
        nginx_config.client_max_body_size = str(nginx.get("client_max_body_size", nginx_config.client_max_body_size))

    def _apply_letsencrypt(self) -> None:
        letsencrypt = self.data.get("letsencrypt") or {}
        if not letsencrypt:
            return
        letsencrypt_config = self.config.letsencrypt
        letsencrypt_config.email = str(letsencrypt.get("email", letsencrypt_config.email))
        if "webroot_path" in letsencrypt:
            letsencrypt_config.webroot_path = Path(str(letsencrypt["webroot_path"]))

    def _apply_volume(self) -> None:
        volume = self.data.get("volume") or {}
        if not volume:
            return
        volume_config = self.config.volume
        volume_config.benches.reservation = str(volume.get("benches_reservation", volume_config.benches.reservation))
        volume_config.benches.quota = str(volume.get("benches_quota", volume_config.benches.quota))
        volume_config.mariadb.reservation = str(volume.get("mariadb_reservation", volume_config.mariadb.reservation))
        volume_config.mariadb.quota = str(volume.get("mariadb_quota", volume_config.mariadb.quota))

    def _apply_production(self) -> str | None:
        production = self.data.get("production") or {}
        if not production:
            return None
        if "process_manager" in production:
            process_manager = str(production["process_manager"])
            if process_manager not in ("none", "supervisor", "systemd"):
                return "process_manager must be none, supervisor, or systemd"
            self.config.production.process_manager = process_manager
        self.config.production.nginx = bool(production.get("nginx", self.config.production.nginx))
        return None


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


# ── Response ──────────────────────────────────────────────────────────────────


def _build_settings_response(config: BenchConfig) -> dict:
    volume = config.volume
    return {
        "is_linux": is_linux(),
        "bench": {"name": config.name, "python": config.python_version, "http_port": config.http_port, "socketio_port": config.socketio_port, "default_branch": config.default_branch},
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
            "version": config.mariadb.version or "",
        },
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port, "socketio_port": config.redis.socketio_port, "version": config.redis.version or ""},
        "workers": {"default": config.workers.default_count, "short": config.workers.short_count, "long": config.workers.long_count},
        "nginx": {
            "http_port": config.nginx.http_port,
            "https_port": config.nginx.https_port,
            "config_dir": str(config.nginx.config_dir),
            "worker_processes": config.nginx.worker_processes,
            "client_max_body_size": config.nginx.client_max_body_size,
        },
        "letsencrypt": {"email": config.letsencrypt.email, "webroot_path": str(config.letsencrypt.webroot_path)},
        "production": {"process_manager": config.production.process_manager, "nginx": config.production.nginx},
        "volume": {
            "pool": volume.pool,
            "backing": volume.backing,
            "device": volume.device,
            "image_size": volume.image.size,
            "image_path": volume.image_path if volume.backing == "image" else "",
            "benches_reservation": volume.benches.reservation,
            "benches_quota": volume.benches.quota,
            "mariadb_reservation": volume.mariadb.reservation,
            "mariadb_quota": volume.mariadb.quota,
            "mariadb_data_dir": volume.mariadb.data_dir,
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@settings_bp.route("/")
def get_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(_build_settings_response(config))


@settings_bp.route("/", methods=["PATCH"])
def update_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    volume_manager = VolumeManager(config.volume)
    old_restart = _restart_trigger_values(config)

    if error := ConfigPatcher(config, data).apply():
        return jsonify({"ok": False, "error": error}), 400

    if error := volume_manager.validate_sizes_fit_backing():
        return jsonify({"ok": False, "error": error}), 400
    if error := volume_manager.validate_quotas_above_usage():
        return jsonify({"ok": False, "error": error}), 400

    try:
        (bench_root / "bench.toml").write_text(bench_config_to_toml(config))
    except Exception as error:
        return jsonify({"ok": False, "error": f"Failed to write config: {error}"}), 500

    zfs_error = volume_manager.apply_sizes()

    restarted, restart_error = False, None
    if _needs_restart(old_restart, _restart_trigger_values(config)):
        try:
            _regenerate_configs(bench_root, config)
        except Exception as error:
            return jsonify({"ok": False, "error": f"Failed to regenerate configs: {error}"}), 500
        restarted, restart_error = _do_restart(bench_root, config)

    return jsonify({"ok": True, "restarted": restarted, "restart_error": restart_error, "zfs_error": zfs_error})
