from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from pilot.config.bench_toml import dumps_config
from pilot.config.bench import BenchConfig
from pilot.config.worker import WorkerConfig, WorkerGroup

# Wizard-editable flat key -> BenchConfig attribute path.
FLAT_KEYS = {
    "bench_name": "name",
    "python": "python_version",
    "socketio_backend": "socketio_backend",
    "watch_apps_js": "watch_apps_js",
    "reload_python": "reload_python",
    "watch_admin_js": "watch_admin_js",
    "db_type": "db_type",
    "mariadb_password": "mariadb.root_password",
    "mariadb_admin_user": "mariadb.admin_user",
    "mariadb_socket_path": "mariadb.socket_path",
    "mariadb_host": "mariadb.host",
    "mariadb_existing": "mariadb.existing",
    # DB ports are flat settings but not offset-managed; the server is shared.
    "mariadb_port": "mariadb.port",
    "postgres_password": "postgres.root_password",
    "postgres_admin_user": "postgres.admin_user",
    "postgres_port": "postgres.port",
    "postgres_host": "postgres.host",
    "postgres_existing": "postgres.existing",
    "admin_enabled": "admin.enabled",
    "admin_password": "admin.password",
    "admin_domain": "admin.domain",
    "admin_tls": "admin.tls",
    "admin_jwks_url": "admin.jwks_url",
    "admin_jwks_audience": "admin.jwks_audience",
    "admin_allow_bench_management": "admin.allow_bench_management",
    "letsencrypt_email": "letsencrypt.email",
    "production_process_manager": "production.process_manager",
}

# Framework branches the setup wizard offers, newest/recommended first.
FRAMEWORK_BRANCHES = ["version-16", "develop"]

_DEFAULT_DATA: dict = {
    "bench": {"name": "", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": FRAMEWORK_BRANCHES[0]}],
    "mariadb": {"root_password": "root"},
}


def _default_config(name: str = "") -> BenchConfig:
    data = copy.deepcopy(_DEFAULT_DATA)
    data["bench"]["name"] = name
    return BenchConfig._from_dict(data)


# Offset-managed ports stay out of wizard/settings input.
_PORT_FIELDS = ("http_port", "socketio_port", "redis.cache_port", "redis.queue_port", "admin.port")


def default_ports() -> dict[str, int]:
    """Default value for every port field, keyed by its dotted BenchConfig path."""
    config = _default_config()
    return {field: _get_path(config, field) for field in _PORT_FIELDS}


def current_port_offset(toml_path: Path) -> int:
    """Return the offset already baked into an existing bench.toml."""
    if not toml_path.exists():
        return 0
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("bench", {}).get("http_port", default_ports()["http_port"]) - default_ports()["http_port"]
    except (OSError, tomllib.TOMLDecodeError):
        return 0


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
        # Store the manager preference only. Production is enabled (and the
        # deployment built) by `bench setup production`, never by editing config.
        config.production.process_manager = "" if str(value) in ("", "none") else str(value)
    # unknown keys (wizard extras like is_linux) are ignored


def _workers_to_groups(value) -> list[WorkerGroup]:
    """Build worker groups from list or comma-separated queue settings."""
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


def _flatten(config: BenchConfig) -> dict:
    settings = {key: _get_path(config, path) for key, path in FLAT_KEYS.items()}
    app = config.framework_app
    settings["app_repo"] = app.repo
    settings["app_branch"] = app.branch
    settings["workers"] = [{"queues": list(g.queues), "count": g.count} for g in config.workers.groups]
    settings["production_process_manager"] = config.production.process_manager or "none"
    return settings


class BenchTomlBuilder:
    """Translates flat wizard/settings input to BenchConfig."""

    DEFAULTS = {key: value for key, value in _flatten(_default_config()).items() if key != "bench_name"}

    def __init__(self, name: str, settings: dict | None = None, port_offset: int = 0) -> None:
        self._name = name
        self._settings = settings or {}
        self._port_offset = port_offset

    def build(self) -> BenchConfig:
        config = _default_config(self._name)
        self.apply_to(config)
        if self._port_offset:
            for field in _PORT_FIELDS:
                _set_path(config, field, _get_path(config, field) + self._port_offset)
        return config

    def apply_to(self, config: BenchConfig) -> None:
        for key, value in self._settings.items():
            _apply_setting(config, key, value)
        if self._name:
            config.name = self._name

    def render(self) -> str:
        return dumps_config(self.build())

    @classmethod
    def read_settings(cls, toml_path: Path) -> dict:
        """Read bench.toml into the same flat-dict format as DEFAULTS."""
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
        return _flatten(BenchConfig._from_dict(data))
