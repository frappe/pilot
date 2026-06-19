from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.toml_writer import bench_config_to_toml
from bench_cli.config.worker_config import WorkerConfig, WorkerGroup

# The single registry of wizard-editable settings: flat key -> attribute path on
# BenchConfig. Defaults and serialization live in the dataclasses and
# bench_config_to_toml; adding a config field means adding the dataclass field
# (+ its toml_writer line) and, if it is wizard-editable, one entry here.
FLAT_KEYS = {
    "bench_name": "name",
    "python": "python_version",
    "socketio_backend": "socketio_backend",
    "mariadb_password": "mariadb.root_password",
    "mariadb_admin_user": "mariadb.admin_user",
    "mariadb_instance": "mariadb.instance",
    "mariadb_socket_path": "mariadb.socket_path",
    "mariadb_data_dir": "mariadb.data_dir",
    "mariadb_port": "mariadb.port",
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

# Framework branches the setup wizard offers, newest/recommended first. The
FRAMEWORK_BRANCHES = ["develop"]

_DEFAULT_DATA: dict = {
    "bench": {"name": "", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": FRAMEWORK_BRANCHES[0]}],
    "mariadb": {"root_password": "root"},
}


def _default_config(name: str = "") -> BenchConfig:
    data = copy.deepcopy(_DEFAULT_DATA)
    data["bench"]["name"] = name
    return BenchConfig._from_dict(data)


# Ports are managed internally (new benches get an auto-picked offset so they
# don't collide) rather than via FLAT_KEYS, so they stay out of the wizard
# and settings UIs. This is the single place that knows their default values
# and dotted paths — callers needing them (e.g. NewCommand's port offset
# logic) should go through default_ports()/BenchTomlBuilder, not duplicate
# the numbers themselves.
_PORT_FIELDS = ("http_port", "socketio_port", "redis.cache_port", "redis.queue_port", "admin.port", "mariadb.port")


def default_ports() -> dict[str, int]:
    """Default value for every port field, keyed by its dotted BenchConfig path."""
    config = _default_config()
    return {field: _get_path(config, field) for field in _PORT_FIELDS}


def current_port_offset(toml_path: Path) -> int:
    """Offset already baked into an existing bench.toml, derived from its http_port.

    Since ports aren't in FLAT_KEYS, any code that rewrites an existing
    bench.toml via BenchTomlBuilder (e.g. the setup wizard's save step) must
    pass this back in as port_offset, or every other port field silently
    resets to default on the next save.
    """
    if not toml_path.exists():
        return 0
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("bench", {}).get("http_port", default_ports()["http_port"]) - default_ports()["http_port"]
    except Exception:
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
    """Build worker groups from the wizard's ``[{queues, count}, ...]`` list.

    ``queues`` may arrive as a list or a comma-separated string. Empty/invalid
    input falls back to the default groups.
    """
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
    """Adapter between the wizard's flat settings dicts and ``BenchConfig``.

    ``BenchConfig`` + ``bench_config_to_toml`` are the single source of truth
    for defaults and serialization; this class only translates flat keys.
    """

    DEFAULTS = {key: value for key, value in _flatten(_default_config()).items() if key != "bench_name"}
    DEFAULTS["volume_image_size"] = DEFAULTS["volume_image_size"] or "60G"

    def __init__(self, name: str, settings: dict | None = None, port_offset: int = 0) -> None:
        self._name = name
        self._settings = settings or {}
        self._port_offset = port_offset

    def render(self) -> str:
        config = _default_config(self._name)
        for key, value in self._settings.items():
            _apply_setting(config, key, value)
        if self._port_offset:
            for field in _PORT_FIELDS:
                _set_path(config, field, _get_path(config, field) + self._port_offset)
        if self._name:
            config.name = self._name
        return bench_config_to_toml(config)

    @classmethod
    def read_settings(cls, toml_path: Path) -> dict:
        """Read bench.toml into the same flat-dict format as DEFAULTS.

        Parse-only (no validation) so a half-configured file can still be read.
        ``bench_name`` is included (empty string if absent so callers can
        substitute a path-based fallback).
        """
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
        return _flatten(BenchConfig._from_dict(data))
