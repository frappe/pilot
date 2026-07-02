from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from pilot.config.bench_config import BenchConfig
from pilot.config.firewall_config import FirewallRule
from pilot.config.s3_config import S3Config
from pilot.config.toml_store import BenchTomlStore
from pilot.config.worker_config import WorkerGroup
from pilot.core.bench import Bench
from pilot.managers.redis_manager import RedisManager
from pilot.managers.volume_manager import VolumeManager
from pilot.platform import is_linux, native_process_manager

settings_bp = Blueprint("settings", __name__)

_RESTART_KEYS = {
    ("bench", "python"),
    ("bench", "http_port"),
    ("bench", "socketio_port"),
    ("mariadb", "host"),
    ("mariadb", "port"),
    ("mariadb", "admin_user"),
    ("mariadb", "socket_path"),
    # postgres is intentionally absent: its connection is read fresh by the
    # new-site subprocess, so no running process needs restarting on a change.
    ("redis", "cache_port"),
    ("redis", "queue_port"),
    ("workers", "groups"),
    ("production", "process_manager"),
}


def _needs_restart(old: dict, new: dict) -> bool:
    return any(old.get(section, {}).get(key) != new.get(section, {}).get(key) for section, key in _RESTART_KEYS)


def _worker_groups_payload(config: BenchConfig) -> list[dict]:
    return [{"queues": list(g.queues), "count": g.count} for g in config.workers.groups]


def _firewall_payload(config: BenchConfig) -> dict:
    fw = config.firewall
    return {
        "enabled": fw.enabled,
        "default": fw.default,
        "rules": [{"ip": r.ip, "action": r.action, "description": r.description} for r in fw.rules],
    }


def _s3_payload(config: BenchConfig):
    return {
        "access_key": config.s3.access_key,
        "secret_key_set": bool(config.s3.secret_key),
        "bucket": config.s3.bucket,
        "provider": config.s3.provider,
        "region": config.s3.region,
    }


def _s3_provider_options() -> list[dict]:
    from pilot.integrations.s3.base import PROVIDER_LABELS, SUPPORTED_REGIONS

    return [
        {"value": provider, "label": PROVIDER_LABELS[provider], "regions": regions}
        for provider, regions in SUPPORTED_REGIONS.items()
    ]


def _restart_trigger_values(config: BenchConfig) -> dict:
    return {
        "bench": {"python": config.python_version, "http_port": config.http_port, "socketio_port": config.socketio_port},
        "mariadb": {"host": config.mariadb.host, "port": config.mariadb.port, "admin_user": config.mariadb.admin_user, "socket_path": config.mariadb.socket_path},
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port},
        "workers": {"groups": _worker_groups_payload(config)},
        "production": {"process_manager": config.production.process_manager or "none"},
    }


# ── Config patching ───────────────────────────────────────────────────────────


class ConfigPatcher:
    def __init__(self, config: BenchConfig, data: dict) -> None:
        self.config = config
        self.data = data

    def apply(self) -> str | None:
        self._apply_bench()
        self._apply_mariadb()
        self._apply_postgres()
        self._apply_redis()
        self._apply_workers()
        self._apply_firewall()
        self._apply_volume()
        self._apply_admin()
        self._apply_monitor()
        if error := self._apply_s3():
            return error
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

    def _apply_postgres(self) -> None:
        postgres = self.data.get("postgres") or {}
        if not postgres:
            return
        postgres_config = self.config.postgres
        postgres_config.host = str(postgres.get("host", postgres_config.host))
        postgres_config.port = int(postgres.get("port", postgres_config.port))
        postgres_config.admin_user = str(postgres.get("admin_user", postgres_config.admin_user))
        # Password is write-only: never sent to the UI, so update it only when a
        # non-empty value is supplied; otherwise keep the stored one.
        password = str(postgres.get("root_password", "")).strip()
        if password:
            postgres_config.root_password = password

    def _apply_redis(self) -> None:
        redis = self.data.get("redis") or {}
        if not redis:
            return
        redis_config = self.config.redis
        redis_config.cache_port = int(redis.get("cache_port", redis_config.cache_port))
        redis_config.queue_port = int(redis.get("queue_port", redis_config.queue_port))

    def _apply_workers(self) -> None:
        workers = self.data.get("workers")
        if not workers:
            return
        groups = []
        for entry in workers:
            queues = entry.get("queues") or []
            if isinstance(queues, str):
                queues = [q.strip() for q in queues.split(",") if q.strip()]
            queues = [str(q) for q in queues if str(q).strip()]
            if not queues:
                continue
            groups.append(WorkerGroup(queues=queues, count=int(entry.get("count", 1))))
        if groups:
            self.config.workers.groups = groups

    def _apply_firewall(self) -> None:
        firewall = self.data.get("firewall")
        if firewall is None:
            return
        fw = self.config.firewall
        if "enabled" in firewall:
            fw.enabled = bool(firewall["enabled"])
        if "default" in firewall:
            fw.default = str(firewall["default"])
        if "rules" in firewall:
            rules = []
            for entry in firewall["rules"] or []:
                ip = str(entry.get("ip", "")).strip()
                if not ip:
                    continue
                rules.append(
                    FirewallRule(
                        ip=ip,
                        action=str(entry.get("action", "deny")),
                        description=str(entry.get("description", "")).strip(),
                    )
                )
            fw.rules = rules

    def _apply_volume(self) -> None:
        volume = self.data.get("volume") or {}
        if not volume:
            return
        volume_config = self.config.volume
        volume_config.dataset.reservation = str(volume.get("reservation", volume_config.dataset.reservation))
        volume_config.dataset.quota = str(volume.get("quota", volume_config.dataset.quota))

    def _apply_admin(self) -> None:
        """TLS termination is opt-in: persisting tls=true only records the intent;
        the caller runs `setup-letsencrypt` to actually obtain certs and rewrite
        nginx with the HTTP→HTTPS redirect. The email is the ACME account address."""
        admin = self.data.get("admin") or {}
        if "tls" in admin:
            self.config.admin.tls = bool(admin["tls"])
        letsencrypt = self.data.get("letsencrypt") or {}
        if "email" in letsencrypt:
            self.config.letsencrypt.email = str(letsencrypt["email"]).strip()

    def _apply_s3(self) -> str | None:
        s3 = self.data.get("s3") or {}
        if not s3:
            return None
        if s3.get("disconnect"):
            self.config.s3 = S3Config()
            return None
        s3_config = self.config.s3
        if "access_key" in s3:
            s3_config.access_key = str(s3["access_key"]).strip()
        # Secret key is write-only: never sent to the UI, so update it only when
        # a non-empty value is supplied; otherwise keep the stored one.
        secret_key = str(s3.get("secret_key", "")).strip()
        if secret_key:
            s3_config.secret_key = secret_key
        if "bucket" in s3:
            s3_config.bucket = str(s3["bucket"]).strip()
        if "provider" in s3:
            s3_config.provider = str(s3["provider"]).strip()
        if "region" in s3:
            s3_config.region = str(s3["region"]).strip()

        if not (s3_config.access_key or s3_config.secret_key or s3_config.bucket or s3_config.provider or s3_config.region):
            return None

        if not (s3_config.access_key and s3_config.secret_key and s3_config.bucket and s3_config.provider and s3_config.region):
            return "s3.access_key, s3.secret_key, s3.bucket, s3.provider, and s3.region are all required."

        from pilot.integrations.s3.base import SUPPORTED_REGIONS

        if s3_config.provider not in SUPPORTED_REGIONS:
            return f"s3.provider must be one of: {', '.join(SUPPORTED_REGIONS)}"
        if s3_config.region not in SUPPORTED_REGIONS[s3_config.provider]:
            return f"s3.region '{s3_config.region}' is not valid for provider '{s3_config.provider}'."

        return None

    def _apply_monitor(self) -> None:
        monitor = self.data.get("monitor") or {}
        if not monitor:
            return
        from pathlib import Path as _Path

        mon = self.config.monitor
        if "system_log_path" in monitor and str(monitor["system_log_path"]).strip():
            mon.system_log_path = _Path(str(monitor["system_log_path"]).strip())
        if "log_path" in monitor:
            val = str(monitor["log_path"]).strip()
            mon.log_path = _Path(val) if val else None
        if "system_log_max_size" in monitor and str(monitor["system_log_max_size"]).strip():
            mon.system_log_max_size = str(monitor["system_log_max_size"]).strip()
        if "application_log_max_size" in monitor and str(monitor["application_log_max_size"]).strip():
            mon.application_log_max_size = str(monitor["application_log_max_size"]).strip()

    def _apply_production(self) -> str | None:
        production = self.data.get("production") or {}
        if not production:
            return None
        if "process_manager" in production:
            from pilot.config.production_config import VALID_PROCESS_MANAGERS
            from pilot.platform import is_alpine

            process_manager = str(production["process_manager"])
            valid = ("none", *VALID_PROCESS_MANAGERS)
            if process_manager not in valid:
                return f"process_manager must be one of: {', '.join(valid)}"
            pm = "" if process_manager == "none" else process_manager
            if is_alpine() and pm == "systemd":
                # Alpine has no systemd; coerce a stale systemd request to OpenRC
                # (the native Alpine manager), matching the new-bench endpoint, so
                # a cached client default can't break the deployment.
                pm = "openrc"
            self.config.production.process_manager = pm
            self.config.production.enabled = pm != ""
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
    from pilot.core.bench import Bench
    from pilot.managers.process_manager import ProcessManager
    from pilot.managers.redis_manager import RedisManager

    bench = Bench(config, bench_root)
    RedisManager(config.redis, bench).generate_configs()
    ProcessManager.for_bench(bench).write_config()


def _regenerate_nginx(bench_root: Path, config: BenchConfig) -> None:
    """Rewrite this bench's nginx vhosts from config and reload — the nginx slice
    of `setup production`, with no process-manager/workload restart. Applies the
    firewall allow/deny rules live."""
    from pilot.core.bench import Bench
    from pilot.managers.nginx_manager import NginxManager

    bench = Bench(config, bench_root)
    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=True)
    manager.install_config()
    manager.reload()


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


def _restart_openrc(manager) -> tuple[bool, str | None]:
    if not manager.is_running():
        return False, None
    # Configs were regenerated already; re-link any new services (e.g. an added
    # worker group) before restarting the workload. The admin service is left
    # running so the control plane stays reachable across the restart.
    try:
        manager.install_config()
        manager.restart()
    except Exception as error:
        return False, str(error)
    return True, None


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
    from pilot.core.bench import Bench
    from pilot.managers.process_manager import ProcessManager
    from pilot.managers.process_managers.openrc import OpenRCProcessManager
    from pilot.managers.process_managers.supervisor import SupervisorProcessManager
    from pilot.managers.process_managers.systemd import SystemdProcessManager

    bench = Bench(config, bench_root)
    manager = ProcessManager.detect_running(bench)
    if isinstance(manager, SupervisorProcessManager):
        return _restart_supervisor(manager, config.name)
    if isinstance(manager, SystemdProcessManager):
        return _restart_systemd(manager)
    if isinstance(manager, OpenRCProcessManager):
        return _restart_openrc(manager)
    return False, None


# ── Response ──────────────────────────────────────────────────────────────────


def _build_settings_response(config: BenchConfig) -> dict:
    volume = config.volume
    return {
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        "bench": {
            "name": config.name,
            "python": config.python_version,
            "http_port": config.http_port,
            "socketio_port": config.socketio_port,
            "default_branch": config.default_branch,
            "db_type": config.db_type,
        },
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
            "version": config.mariadb.version or "",
        },
        "postgres": {
            "host": config.postgres.host,
            "port": config.postgres.port,
            "admin_user": config.postgres.admin_user,
            "password_set": bool(config.postgres.root_password),
        },
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port, "version": RedisManager.installed_version() or config.redis.version or ""},
        "workers": _worker_groups_payload(config),
        "firewall": _firewall_payload(config),
        "production": {"process_manager": config.production.process_manager or "none"},
        "admin": {"domain": config.admin.domain, "tls": config.admin.tls},
        "letsencrypt": {"email": config.letsencrypt.email},
        "s3": _s3_payload(config),
        "s3_providers": _s3_provider_options(),
        "volume": {
            "pool": volume.pool,
            "backing": volume.backing,
            "device": volume.device,
            "image_size": volume.image.size,
            "image_path": volume.image_path if volume.backing == "image" else "",
            "reservation": volume.dataset.reservation,
            "quota": volume.dataset.quota,
        },
        "monitor": {
            "system_log_path": str(config.monitor.system_log_path),
            "log_path": str(config.monitor.log_path) if config.monitor.log_path else "",
            "system_log_max_size": config.monitor.system_log_max_size,
            "application_log_max_size": config.monitor.application_log_max_size,
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@settings_bp.route("/")
def get_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify(_build_settings_response(config))


@settings_bp.route("/my-ip")
def my_ip():
    """The requesting client's IP, so the UI can tell the operator which address to
    allow-list before blocking by default. nginx passes the real client address
    (IPv4 or IPv6) as X-Real-IP."""
    ip = request.headers.get("X-Real-IP") or request.remote_addr or ""
    return jsonify({"ip": ip})


@settings_bp.route("/", methods=["PATCH"])
def update_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    store = BenchTomlStore.for_bench(bench_root)
    try:
        config = store.read()
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    volume_manager = VolumeManager(config.volume)
    old_restart = _restart_trigger_values(config)
    old_firewall = _firewall_payload(config)
    old_s3_config = _s3_payload(config)

    if error := ConfigPatcher(config, data).apply():
        return jsonify({"ok": False, "error": error}), 400

    if config.volume.enabled:
        if error := volume_manager.validate_sizes_fit_backing():
            return jsonify({"ok": False, "error": error}), 400
        if error := volume_manager.validate_quotas_above_usage():
            return jsonify({"ok": False, "error": error}), 400

    try:
        store.write(config)
    except Exception as error:
        return jsonify({"ok": False, "error": f"Failed to write config: {error}"}), 500

    zfs_error = volume_manager.apply_sizes() if config.volume.enabled else None

    restarted, restart_error = False, None
    if _needs_restart(old_restart, _restart_trigger_values(config)):
        try:
            _regenerate_configs(bench_root, config)
        except Exception as error:
            return jsonify({"ok": False, "error": f"Failed to regenerate configs: {error}"}), 500
        restarted, restart_error = _do_restart(bench_root, config)

    # Firewall rules only affect nginx: regenerate + reload the vhosts (no
    # workload restart). Only meaningful once the bench is in production.
    nginx_error = None
    if config.production.enabled and _firewall_payload(config) != old_firewall:
        try:
            _regenerate_nginx(bench_root, config)
        except Exception as error:
            nginx_error = str(error)

    # Sync common site config with the patched s3 settings
    if _s3_payload(config) != old_s3_config:
        bench = Bench(BenchConfig.from_file(bench_root / "bench.toml"), bench_root)
        bench.sync_s3_credentials(config.s3)

    return jsonify(
        {
            "ok": True,
            "restarted": restarted,
            "restart_error": restart_error,
            "zfs_error": zfs_error,
            "nginx_error": nginx_error,
        }
    )
