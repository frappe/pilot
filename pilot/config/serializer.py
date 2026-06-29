"""Serialise a BenchConfig to bench.toml and translate the wizard's flat-key
settings dicts ⇄ BenchConfig. The single home for both directions; BenchTomlStore
does the file I/O on top of this."""

from __future__ import annotations

import copy

from pilot.config.bench_config import BenchConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup


def to_toml(config: BenchConfig) -> str:
    parts: list[str] = []

    parts.append("[bench]")
    parts.append(f'name = "{config.name}"')
    parts.append(f'python = "{config.python_version}"')
    parts.append(f"http_port = {config.http_port}")
    parts.append(f"socketio_port = {config.socketio_port}")
    parts.append(f'socketio_backend = "{config.socketio_backend}"')
    parts.append(f'db_type = "{config.db_type}"')
    if config.default_branch:
        parts.append(f'default_branch = "{config.default_branch}"')
    parts.append("")

    for app in config.apps:
        parts.append("[[apps]]")
        parts.append(f'name = "{app.name}"')
        parts.append(f'repo = "{app.repo}"')
        parts.append(f'branch = "{app.branch}"')
        if app.branches:
            branches_str = ", ".join(f'"{b}"' for b in app.branches)
            parts.append(f"branches = [{branches_str}]")
        parts.append("")

    m = config.mariadb
    parts.append("[mariadb]")
    parts.append(f'host = "{m.host}"')
    parts.append(f"port = {m.port}")
    parts.append(f'root_password = "{m.root_password}"')
    parts.append(f'admin_user = "{m.admin_user}"')
    parts.append(f'socket_path = "{m.socket_path}"')
    if m.version:
        parts.append(f'version = "{m.version}"')
    if m.instance:
        parts.append(f'instance = "{m.instance}"')
    if m.data_dir:
        parts.append(f'data_dir = "{m.data_dir}"')
    parts.append("")

    pg = config.postgres
    parts.append("[postgres]")
    parts.append(f'host = "{pg.host}"')
    parts.append(f"port = {pg.port}")
    parts.append(f'root_password = "{pg.root_password}"')
    parts.append(f'admin_user = "{pg.admin_user}"')
    if pg.version:
        parts.append(f'version = "{pg.version}"')
    if pg.instance:
        parts.append(f'instance = "{pg.instance}"')
    parts.append("")

    r = config.redis
    parts.append("[redis]")
    parts.append(f"cache_port = {r.cache_port}")
    parts.append(f"queue_port = {r.queue_port}")
    if r.version:
        parts.append(f'version = "{r.version}"')
    parts.append("")

    for group in config.workers.groups:
        parts.append("[[workers]]")
        queues = ", ".join(f'"{q}"' for q in group.queues)
        parts.append(f"queues = [{queues}]")
        parts.append(f"count = {group.count}")
        parts.append("")

    p = config.production
    parts.append("[production]")
    parts.append(f"enabled = {'true' if p.enabled else 'false'}")
    if p.process_manager:
        parts.append(f'process_manager = "{p.process_manager}"')
    parts.append(f"use_companion_manager = {'true' if p.use_companion_manager else 'false'}")
    parts.append("")

    g = config.gunicorn
    parts.append("[gunicorn]")
    parts.append(f"workers = {g.workers}")
    parts.append(f"threads = {g.threads}")
    parts.append(f"timeout = {g.timeout}")
    parts.append(f'worker_class = "{g.worker_class}"')
    parts.append(f"malloc_arena_max = {g.malloc_arena_max or 2}")
    parts.append(f"max_requests = {g.max_requests}")
    parts.append(f"max_requests_jitter = {g.max_requests_jitter}")
    parts.append("")

    le = config.letsencrypt
    parts.append("[letsencrypt]")
    parts.append(f'email = "{le.email}"')
    parts.append(f'webroot_path = "{le.webroot_path}"')
    parts.append("")

    a = config.admin
    parts.append("[admin]")
    parts.append(f"port = {a.port}")
    parts.append(f"timeout = {a.timeout}")
    parts.append(f"enabled = {'true' if a.enabled else 'false'}")
    parts.append(f'password = "{a.password}"')
    if a.jwt_secret:
        parts.append(f'jwt_secret = "{a.jwt_secret}"')
    parts.append(f'domain = "{a.domain}"')
    parts.append(f"tls = {'true' if a.tls else 'false'}")
    parts.append("")

    v = config.volume
    if v.enabled:
        parts.append("[volume]")
        parts.append(f'pool = "{v.pool}"')
        parts.append(f'backing = "{v.backing}"')
        if v.backing == "image":
            parts.append("")
            parts.append("[volume.image]")
            parts.append(f'size = "{v.image.size}"')
            parts.append(f'path = "{v.image_path}"')
        elif v.backing == "device":
            parts.append(f'device = "{v.device}"')
        # backing = "auto" carries no device/image fields — resolved during bench init
        parts.append("")
        parts.append("[volume.dataset]")
        parts.append(f'reservation = "{v.dataset.reservation}"')
        parts.append(f'quota = "{v.dataset.quota}"')
        parts.append("")

    return "\n".join(parts)


# ── Wizard flat-key translation ─────────────────────────────────────────────────

# Wizard-editable settings: flat key -> dotted attribute path on BenchConfig.
FLAT_KEYS = {
    "bench_name": "name",
    "python": "python_version",
    "socketio_backend": "socketio_backend",
    "db_type": "db_type",
    "mariadb_password": "mariadb.root_password",
    "mariadb_admin_user": "mariadb.admin_user",
    "mariadb_instance": "mariadb.instance",
    "mariadb_socket_path": "mariadb.socket_path",
    "mariadb_data_dir": "mariadb.data_dir",
    # mariadb.port is NOT here: like the other ports it is offset-managed via
    # PORT_FIELDS. postgres.port is safe — shared server, never offset.
    "postgres_password": "postgres.root_password",
    "postgres_admin_user": "postgres.admin_user",
    "postgres_port": "postgres.port",
    "postgres_instance": "postgres.instance",
    "admin_enabled": "admin.enabled",
    "admin_password": "admin.password",
    "admin_domain": "admin.domain",
    "admin_tls": "admin.tls",
    "letsencrypt_email": "letsencrypt.email",
    "volume_enabled": "volume.enabled",
    "volume_pool": "volume.pool",
    "volume_backing": "volume.backing",
    "volume_device": "volume.device",
    "volume_image_size": "volume.image.size",
    "volume_image_path": "volume.image.path",
    "volume_reservation": "volume.dataset.reservation",
    "volume_quota": "volume.dataset.quota",
    "production_process_manager": "production.process_manager",
}

# Ports get an auto-picked offset per bench so they don't collide; kept out of
# FLAT_KEYS so the wizard/settings can't touch them (only the offset can).
PORT_FIELDS = ("http_port", "socketio_port", "redis.cache_port", "redis.queue_port", "admin.port", "mariadb.port")

# Framework branches the setup wizard offers, newest/recommended first.
FRAMEWORK_BRANCHES = ["develop"]

_DEFAULT_DATA: dict = {
    "bench": {"name": "", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": FRAMEWORK_BRANCHES[0]}],
    "mariadb": {"root_password": "root"},
}


def default_config(name: str = "") -> BenchConfig:
    data = copy.deepcopy(_DEFAULT_DATA)
    data["bench"]["name"] = name
    return BenchConfig._from_dict(data)


def default_ports() -> dict[str, int]:
    """Default value for every port field, keyed by its dotted BenchConfig path."""
    config = default_config()
    return {field: _get_path(config, field) for field in PORT_FIELDS}


def _get_path(config: BenchConfig, path: str):
    obj = config
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set_path(config: BenchConfig, path: str, value) -> None:
    *parents, leaf = path.split(".")
    obj = config
    for part in parents:
        obj = getattr(obj, part)
    current = getattr(obj, leaf)
    if isinstance(current, bool):
        value = bool(value)
    elif isinstance(current, int):
        value = int(value)
    elif isinstance(current, str):
        value = str(value)
    setattr(obj, leaf, value)


def _workers_to_groups(value) -> list[WorkerGroup]:
    """Worker groups from the wizard's ``[{queues, count}, ...]`` list. ``queues``
    may be a list or comma-separated string; empty/invalid falls back to defaults."""
    if not isinstance(value, list) or not value:
        return WorkerConfig().groups
    groups = []
    for entry in value:
        queues = entry.get("queues") or []
        if isinstance(queues, str):
            queues = [q.strip() for q in queues.split(",") if q.strip()]
        queues = [str(q) for q in queues if str(q).strip()]
        if not queues:
            continue
        groups.append(WorkerGroup(queues=queues, count=max(1, int(entry.get("count", 1)))))
    return groups or WorkerConfig().groups


def _apply_setting(config: BenchConfig, key: str, value) -> None:
    if key in FLAT_KEYS:
        _set_path(config, FLAT_KEYS[key], value)
    elif key == "app_repo":
        config.apps[0].repo = str(value)
    elif key == "app_branch":
        config.apps[0].branch = str(value)
    elif key == "workers":
        config.workers.groups = _workers_to_groups(value)
    elif key == "production_process_manager":
        # Store the manager preference only; production is enabled by
        # `bench setup production`, never by editing config.
        config.production.process_manager = "" if str(value) in ("", "none") else str(value)
    # unknown keys (wizard extras like is_linux) are ignored


def flatten(config: BenchConfig) -> dict:
    """BenchConfig → the wizard's flat-key settings dict."""
    settings = {key: _get_path(config, path) for key, path in FLAT_KEYS.items()}
    app = config.framework_app
    settings["app_repo"] = app.repo
    settings["app_branch"] = app.branch
    settings["workers"] = [{"queues": list(g.queues), "count": g.count} for g in config.workers.groups]
    settings["production_process_manager"] = config.production.process_manager or "none"
    return settings


def build(name: str, settings: dict | None = None, port_offset: int = 0) -> BenchConfig:
    """Flat-key settings dict → BenchConfig (the wizard/new-bench builder)."""
    config = default_config(name)
    for key, value in (settings or {}).items():
        _apply_setting(config, key, value)
    if port_offset:
        for field in PORT_FIELDS:
            _set_path(config, field, _get_path(config, field) + port_offset)
    if name:
        config.name = name
    return config


# Wizard defaults: every flat key's default value except the bench name.
WIZARD_DEFAULTS = {key: value for key, value in flatten(default_config()).items() if key != "bench_name"}
WIZARD_DEFAULTS["volume_image_size"] = WIZARD_DEFAULTS["volume_image_size"] or "60G"
