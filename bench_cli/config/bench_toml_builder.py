from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.toml_writer import bench_config_to_toml

# The single registry of wizard-editable settings: flat key -> attribute path on
# BenchConfig. Defaults and serialization live in the dataclasses and
# bench_config_to_toml; adding a config field means adding the dataclass field
# (+ its toml_writer line) and, if it is wizard-editable, one entry here.
FLAT_KEYS = {
    "bench_name": "name",
    "python": "python_version",
    "socketio_backend": "socketio_backend",
    "mariadb_password": "mariadb.root_password",
    "admin_enabled": "admin.enabled",
    "admin_port": "admin.port",
    "admin_password": "admin.password",

    "volume_pool": "volume.pool",
    "volume_backing": "volume.backing",
    "volume_device": "volume.device",
    "volume_image_size": "volume.image.size",
    "volume_image_path": "volume.image.path",
    "volume_benches_reservation": "volume.benches.reservation",
    "volume_benches_quota": "volume.benches.quota",
    "volume_mariadb_reservation": "volume.mariadb.reservation",
    "volume_mariadb_quota": "volume.mariadb.quota",
    "volume_mariadb_data_dir": "volume.mariadb.data_dir",
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
    # unknown keys (wizard extras like is_linux) are ignored


def _flatten(config: BenchConfig) -> dict:
    settings = {key: _get_path(config, path) for key, path in FLAT_KEYS.items()}
    app = config.framework_app
    settings["app_repo"] = app.repo
    settings["app_branch"] = app.branch
    return settings


class BenchTomlBuilder:
    """Adapter between the wizard's flat settings dicts and ``BenchConfig``.

    ``BenchConfig`` + ``bench_config_to_toml`` are the single source of truth
    for defaults and serialization; this class only translates flat keys.
    """

    DEFAULTS = {key: value for key, value in _flatten(_default_config()).items() if key != "bench_name"}
    DEFAULTS["volume_image_size"] = DEFAULTS["volume_image_size"] or "60G"

    def __init__(self, name: str, settings: dict | None = None) -> None:
        self._name = name
        self._settings = settings or {}

    def render(self) -> str:
        config = _default_config(self._name)
        for key, value in self._settings.items():
            _apply_setting(config, key, value)
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
