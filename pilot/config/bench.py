from __future__ import annotations

import copy
import re
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

from pilot.config.admin import AdminConfig
from pilot.config.app import AppConfig
from pilot.config.central import CentralConfig
from pilot.config.firewall import FirewallConfig, FirewallRule
from pilot.config.gunicorn import GunicornConfig
from pilot.config.letsencrypt import LetsEncryptConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.config.monitor import MonitorConfig
from pilot.config.nginx import NginxConfig
from pilot.config.postgres import PostgresConfig
from pilot.config.production import ProductionConfig
from pilot.config.redis import RedisConfig
from pilot.config.s3 import S3Config
from pilot.config.waf import WafCondition, WafConfig, WafRule
from pilot.config.worker import WorkerConfig, WorkerGroup
from pilot.exceptions import ConfigError
from pilot.internal.atomic_file import (
    atomic_write_private_text,
    exclusive_file_lock,
    replace_private_text_locked,
)
from pilot.internal.toml import ConfigDict, Toml

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_PORT_MIN = 1
_PORT_MAX = 65535

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
    "apps": [
        {
            "name": "frappe",
            "repo": "https://github.com/frappe/frappe",
            "branch": FRAMEWORK_BRANCHES[0],
        }
    ],
    "mariadb": {"root_password": "root"},
}

# Offset-managed ports stay out of wizard/settings input.
_PORT_FIELDS = ("http_port", "socketio_port", "redis.cache_port", "redis.queue_port", "admin.port")


@dataclass
class BenchConfig:
    """A bench's full configuration: fields, validation, TOML persistence,
    and the setup wizard's flat-key view, all in one place."""

    FILENAME: ClassVar[str] = "bench.toml"

    name: str
    python_version: str
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    apps: list[AppConfig] = field(default_factory=list)
    http_port: int = 8000
    socketio_port: int = 9000
    socketio_backend: str = "node"
    watch_apps_js: bool = True
    reload_python: bool = True
    watch_admin_js: bool = False
    # The single database engine for this bench's sites: "mariadb" or "postgres".
    db_type: str = "mariadb"
    default_branch: str = ""
    production: ProductionConfig = field(default_factory=ProductionConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    nginx: NginxConfig = field(default_factory=NginxConfig)
    gunicorn: GunicornConfig = field(default_factory=GunicornConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    central: CentralConfig = field(default_factory=CentralConfig)
    firewall: FirewallConfig = field(default_factory=FirewallConfig)
    waf: WafConfig = field(default_factory=WafConfig)
    s3: S3Config = field(default_factory=S3Config)

    # -- construction --

    @classmethod
    def from_file(cls, path: Path) -> "BenchConfig":
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        config = cls._from_dict(data)
        config.validate()
        return config

    @classmethod
    def default(cls, name: str = "") -> "BenchConfig":
        """A fresh config seeded with the setup wizard's baseline values."""
        data = copy.deepcopy(_DEFAULT_DATA)
        data["bench"]["name"] = name
        return cls._from_dict(data)

    @classmethod
    def default_flat_settings(cls) -> dict:
        """Default value for every wizard/settings flat key, except bench_name."""
        return {key: value for key, value in cls.default()._to_flat_dict().items() if key != "bench_name"}

    @classmethod
    def from_flat(cls, name: str, settings: dict | None = None, port_offset: int = 0) -> "BenchConfig":
        """Build a config from the setup wizard/settings API's flat-key input."""
        config = cls.default(name)
        config._apply_flat_settings(settings or {})
        if name:
            config.name = name
        if port_offset:
            for path in _PORT_FIELDS:
                _set_path(config, path, _get_path(config, path) + port_offset)
        return config

    @classmethod
    def _from_dict(cls, data: dict, *, strict: bool = False) -> "BenchConfig":
        cls._report_unknown_fields(data, strict=strict)
        bench_data = data.get("bench", {})
        apps = [
            AppConfig(
                name=a.get("name", ""),
                repo=a.get("repo", ""),
                branch=a.get("branch", ""),
                branches=a.get("branches", []),
            )
            for a in data.get("apps", [])
        ]
        sections = {section.attr: section.read(data) for section in _SECTIONS}
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            socketio_backend=bench_data.get("socketio_backend", "node"),
            watch_apps_js=bench_data.get("watch_apps_js", True),
            reload_python=bench_data.get("reload_python", True),
            watch_admin_js=bench_data.get("watch_admin_js", False),
            db_type=bench_data.get("db_type", "mariadb"),
            default_branch=bench_data.get("default_branch", ""),
            apps=apps,
            **sections,
        )

    @staticmethod
    def _known_fields(dataclass_type: type, data: dict) -> dict:
        """Drop keys a bench.toml table has that the dataclass no longer
        declares, so a config written by an older bench-cli still loads."""
        known = {f.name for f in fields(dataclass_type)}
        return {k: v for k, v in data.items() if k in known}

    @classmethod
    def _report_unknown_fields(cls, data: dict, *, strict: bool) -> None:
        """Unknown keys are ignored so older/foreign configs still load; strict
        (opt-in, for validation) raises ConfigError naming them."""
        if not strict:
            return
        paths = cls._unknown_config_paths(data)
        if paths:
            raise ConfigError(f"bench.toml has unrecognized fields: {', '.join(paths)}")

    # -- validation --

    def validate(self) -> None:
        self._validate_required_fields()
        self._validate_bench_name()
        self._validate_app_names_unique()
        self._validate_ports()
        self._validate_socketio_backend()
        self._validate_db_type()
        self.redis.validate()
        self.workers.validate()
        self.letsencrypt.validate()
        self.gunicorn.validate()
        self.production.validate(self.name)
        self.admin.validate(self.production.enabled, self.name)
        self.firewall.validate()
        self.waf.validate(self.nginx.client_max_body_size)

    def _validate_required_fields(self) -> None:
        if not self.name:
            raise ConfigError("bench.name is required and must not be empty.")
        if not self.python_version:
            raise ConfigError("bench.python is required and must not be empty.")
        for app in self.apps:
            if not app.name or not app.repo or not app.branch:
                raise ConfigError(f"App '{app.name or '(unnamed)'}' must have name, repo, and branch.")
            if app.branches and app.branch not in app.branches:
                raise ConfigError(
                    f"App '{app.name}': active branch '{app.branch}' is not listed in branches {app.branches}."
                )

    def _validate_bench_name(self) -> None:
        if not _BENCH_NAME_PATTERN.match(self.name):
            raise ConfigError(
                f"bench.name '{self.name}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens."
            )

    def _validate_app_names_unique(self) -> None:
        names = [app.name for app in self.apps]
        seen = set()
        for name in names:
            if name in seen:
                raise ConfigError(f"Duplicate app name '{name}'. App names must be unique.")
            seen.add(name)

    def _validate_ports(self) -> None:
        ports = {
            "bench.http_port": self.http_port,
            "bench.socketio_port": self.socketio_port,
            "mariadb.port": self.mariadb.port,
            "postgres.port": self.postgres.port,
        }
        for name, port in ports.items():
            if not (_PORT_MIN <= port <= _PORT_MAX):
                raise ConfigError(
                    f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}."
                )

    def _validate_socketio_backend(self) -> None:
        if self.socketio_backend not in ("python", "node"):
            raise ConfigError(
                f"bench.socketio_backend '{self.socketio_backend}' is invalid. Must be 'python' or 'node'."
            )

    def _validate_db_type(self) -> None:
        if self.db_type not in ("mariadb", "postgres", "sqlite"):
            raise ConfigError(
                f"bench.db_type '{self.db_type}' is invalid. Must be 'mariadb', 'postgres', or 'sqlite'."
            )

    # -- derived data --

    @property
    def framework_app(self) -> AppConfig:
        if not self.apps:
            return AppConfig(name="frappe", repo="", branch="")
        return self.apps[0]

    def get_app_by_name(self, name: str) -> AppConfig:
        for app in self.apps:
            if app.name == name:
                return app
        raise KeyError(f"No app named '{name}' found in config.")

    # -- file store --

    @classmethod
    def toml_path(cls, bench_root: Path) -> Path:
        """Resolve either a bench directory or an explicit bench.toml path."""
        path = Path(bench_root)
        return path / cls.FILENAME if path.is_dir() else path

    @classmethod
    def exists(cls, bench_root: Path) -> bool:
        return cls.toml_path(bench_root).exists()

    @classmethod
    def read(cls, bench_root: Path, *, validate: bool = True, strict: bool = False) -> "BenchConfig":
        """Typed config. ``validate=False`` parses a half-configured file."""
        path = cls.toml_path(bench_root)
        config = cls._from_dict(Toml.loads(path.read_text(encoding="utf-8")), strict=strict)
        if validate:
            config.validate()
        return config

    @classmethod
    def read_raw(cls, bench_root: Path) -> dict:
        """Parsed TOML as a plain dict, preserving every section as written."""
        return Toml.loads(cls.toml_path(bench_root).read_text(encoding="utf-8"))

    @classmethod
    def read_flat(cls, bench_root: Path) -> dict:
        """Wizard's flat-key settings dict (parse-only)."""
        with open(cls.toml_path(bench_root), "rb") as fh:
            data = tomllib.load(fh)
        return cls._from_dict(data)._to_flat_dict()

    def write(self, bench_root: Path) -> None:
        atomic_write_private_text(self.toml_path(bench_root), self._validated_dumps())

    @classmethod
    @contextmanager
    def open(cls, bench_root: Path, mode: str = "rw") -> Iterator:
        """Lock bench.toml for one read-modify-write transaction, writing
        back on exit if changed. mode="rw" yields a typed BenchConfig;
        mode="raw" yields the parsed TOML as a plain dict."""
        if mode not in ("rw", "raw"):
            raise ValueError(f"Unsupported mode: {mode!r}. Use 'rw' or 'raw'.")
        path = cls.toml_path(bench_root)
        with exclusive_file_lock(path):
            if mode == "raw":
                data = Toml.loads(path.read_text(encoding="utf-8"))
                original_data = copy.deepcopy(data)
                yield data
                if data != original_data:
                    content = Toml.dumps(data)
                    cls._validate_serialized(content)
                    replace_private_text_locked(path, content)
            else:
                config = cls.read(bench_root)
                original_config = copy.deepcopy(config)
                yield config
                if config != original_config:
                    replace_private_text_locked(path, config._validated_dumps())

    @classmethod
    def write_flat(cls, bench_root: Path, name: str, settings: dict, port_offset: int = 0) -> None:
        """Atomically apply flat settings without replacing other TOML fields."""
        path = cls.toml_path(bench_root)
        with exclusive_file_lock(path):
            if path.exists():
                original = Toml.loads(path.read_text(encoding="utf-8"))
                config = cls._from_dict(original)
                config._apply_flat_settings(settings)
                if name:
                    config.name = name
                replacement = Toml.loads(config._validated_dumps())
                content = Toml.dumps(cls._preserve_unknown_config(original, replacement))
                cls._validate_serialized(content)
            else:
                content = cls.from_flat(name, settings, port_offset=port_offset)._validated_dumps()
            replace_private_text_locked(path, content)

    @classmethod
    def write_raw(cls, bench_root: Path, data: dict) -> None:
        content = Toml.dumps(data)
        cls._validate_serialized(content)
        atomic_write_private_text(cls.toml_path(bench_root), content)

    @classmethod
    def _validate_serialized(cls, content: str) -> None:
        config = cls._from_dict(Toml.loads(content))
        config.validate()

    def _validated_dumps(self) -> str:
        self.validate()
        content = self.dumps()
        self._validate_serialized(content)
        return content

    # -- TOML serialization --

    def dumps(self) -> str:
        return Toml.dumps(self._to_toml_dict())

    def _to_toml_dict(self) -> ConfigDict:
        data: ConfigDict = {"bench": self._bench_section(), "apps": self._apps_section()}
        for section in _SECTIONS:
            value = section.write(self)
            if value is not None:
                data[section.attr] = value
        return data

    def _bench_section(self) -> ConfigDict:
        bench: ConfigDict = {
            "name": self.name,
            "python": self.python_version,
            "http_port": self.http_port,
            "socketio_port": self.socketio_port,
            "socketio_backend": self.socketio_backend,
            "watch_apps_js": self.watch_apps_js,
            "reload_python": self.reload_python,
            "watch_admin_js": self.watch_admin_js,
            "db_type": self.db_type,
        }
        if self.default_branch:
            bench["default_branch"] = self.default_branch
        return bench

    def _apps_section(self) -> list[ConfigDict]:
        apps: list[ConfigDict] = []
        for app in self.apps:
            app_data: ConfigDict = {"name": app.name, "repo": app.repo, "branch": app.branch}
            if app.branches:
                app_data["branches"] = app.branches
            apps.append(app_data)
        return apps

    def _mariadb_section(self) -> ConfigDict:
        return {
            "host": self.mariadb.host,
            "port": self.mariadb.port,
            "root_password": self.mariadb.root_password,
            "admin_user": self.mariadb.admin_user,
            "socket_path": self.mariadb.socket_path,
            "existing": self.mariadb.existing,
        }

    def _postgres_section(self) -> ConfigDict:
        return {
            "host": self.postgres.host,
            "port": self.postgres.port,
            "root_password": self.postgres.root_password,
            "admin_user": self.postgres.admin_user,
            "existing": self.postgres.existing,
        }

    def _redis_section(self) -> ConfigDict:
        redis: ConfigDict = {
            "cache_port": self.redis.cache_port,
            "queue_port": self.redis.queue_port,
        }
        if self.redis.version:
            redis["version"] = self.redis.version
        return redis

    def _workers_section(self) -> list[ConfigDict]:
        return [{"queues": group.queues, "count": group.count} for group in self.workers.groups]

    def _production_section(self) -> ConfigDict:
        production: ConfigDict = {
            "enabled": self.production.enabled,
            "use_companion_manager": self.production.use_companion_manager,
        }
        if self.production.process_manager:
            production["process_manager"] = self.production.process_manager
        return production

    def _gunicorn_section(self) -> ConfigDict:
        return {
            "workers": self.gunicorn.workers,
            "threads": self.gunicorn.threads,
            "timeout": self.gunicorn.timeout,
            "worker_class": self.gunicorn.worker_class,
            "malloc_arena_max": self.gunicorn.malloc_arena_max or 2,
            "max_requests": self.gunicorn.max_requests,
            "max_requests_jitter": self.gunicorn.max_requests_jitter,
        }

    def _letsencrypt_section(self) -> ConfigDict:
        return {
            "email": self.letsencrypt.email,
            "webroot_path": str(self.letsencrypt.webroot_path),
        }

    def _admin_section(self) -> ConfigDict:
        admin: ConfigDict = {
            "port": self.admin.port,
            "timeout": self.admin.timeout,
            "enabled": self.admin.enabled,
            "password": self.admin.password,
            "domain": self.admin.domain,
            "tls": self.admin.tls,
            "allow_bench_management": self.admin.allow_bench_management,
        }
        optional_admin = {
            "jwt_secret": self.admin.jwt_secret,
            "jwks_url": self.admin.jwks_url,
            "jwks_audience": self.admin.jwks_audience,
        }
        admin.update({key: value for key, value in optional_admin.items() if value})
        return admin

    def _central_section(self) -> ConfigDict:
        return {
            "endpoint": self.central.endpoint,
            "auth_token": self.central.auth_token,
        }

    def _firewall_section(self) -> ConfigDict:
        return {
            "enabled": self.firewall.enabled,
            "default": self.firewall.default,
            "rules": [
                {
                    "ip": rule.ip,
                    "action": rule.action,
                    "description": rule.description,
                }
                for rule in self.firewall.rules
            ],
        }

    def _waf_section(self) -> ConfigDict:
        waf = self.waf
        return {
            "enabled": waf.enabled,
            "mode": waf.mode,
            "paranoia": waf.paranoia,
            "inbound_threshold": waf.inbound_threshold,
            "body_limit": waf.body_limit,
            "inspect_responses": waf.inspect_responses,
            "exclusions": waf.exclusions,
            "exempt_paths": waf.exempt_paths,
            "custom_rules": [
                {
                    "name": rule.name,
                    "action": rule.action,
                    "match": rule.match,
                    "enabled": rule.enabled,
                    "conditions": [
                        {
                            "field": c.field,
                            "operator": c.operator,
                            "value": c.value,
                            "header_name": c.header_name,
                        }
                        for c in rule.conditions
                    ],
                }
                for rule in waf.custom_rules
            ],
        }

    def _s3_section(self) -> ConfigDict:
        return {
            "access_key": self.s3.access_key,
            "secret_key": self.s3.secret_key,
            "bucket": self.s3.bucket,
            "provider": self.s3.provider,
            "region": self.s3.region,
        }

    def _monitor_section(self) -> ConfigDict:
        monitor = self.monitor
        data: ConfigDict = {
            "system_log_path": str(monitor.system_log_path),
            "authority_file_path": str(monitor.authority_file_path),
            "system_log_max_size": monitor.system_log_max_size,
            "application_log_max_size": monitor.application_log_max_size,
        }
        if monitor.log_path:
            data["log_path"] = str(monitor.log_path)
        return data

    # -- wizard flat-key interface --

    def _apply_flat_settings(self, settings: dict) -> None:
        for key, value in settings.items():
            self._apply_setting(key, value)

    def _apply_setting(self, key: str, value) -> None:
        if key in FLAT_KEYS:
            _set_path(self, FLAT_KEYS[key], value)
        elif key == "app_repo":
            self.apps[0].repo = str(value)
        elif key == "app_branch":
            self.apps[0].branch = str(value)
        elif key == "workers":
            self.workers.groups = _workers_to_groups(value)
        elif key == "production_process_manager":
            # Store the manager preference only. Production is enabled (and the
            # deployment built) by `bench setup production`, never by editing config.
            self.production.process_manager = "" if str(value) in ("", "none") else str(value)
        # unknown keys (wizard extras like is_linux) are ignored

    def _to_flat_dict(self) -> dict:
        """Wizard/settings flat-key view of this config."""
        settings = {key: _get_path(self, path) for key, path in FLAT_KEYS.items()}
        app = self.framework_app
        settings["app_repo"] = app.repo
        settings["app_branch"] = app.branch
        settings["workers"] = [{"queues": list(g.queues), "count": g.count} for g in self.workers.groups]
        settings["production_process_manager"] = self.production.process_manager or "none"
        return settings

    # -- wizard defaults --

    @classmethod
    def default_ports(cls) -> dict[str, int]:
        """Default value for every port field, keyed by its dotted attribute path."""
        config = cls.default()
        return {path: _get_path(config, path) for path in _PORT_FIELDS}

    @classmethod
    def current_port_offset(cls, toml_path: Path) -> int:
        """Return the offset already baked into an existing bench.toml."""
        if not toml_path.exists():
            return 0
        try:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            return (
                data.get("bench", {}).get("http_port", cls.default_ports()["http_port"])
                - cls.default_ports()["http_port"]
            )
        except (OSError, tomllib.TOMLDecodeError):
            return 0

    # -- unknown-field schema (older/foreign bench.toml compatibility) --

    @staticmethod
    def _unknown_config_paths(data: Mapping) -> list[str]:
        """Dotted paths of every bench.toml key the schema does not declare, e.g.
        ``mariadb.typo`` or an unknown top-level table ``whatever``."""
        return _scan(data, _SCHEMA_ROOT, "")

    @staticmethod
    def _preserve_unknown_config(original: Mapping, replacement: Mapping) -> dict:
        """Keep fields outside the managed schema when replacing known config."""
        return _preserve_unknown(original, replacement, _SCHEMA_ROOT)


@dataclass(frozen=True)
class _Section:
    """One nested bench.toml table, wired for both reading and writing.

    To add a new nested section: add the field to BenchConfig (with a
    dataclass, plus a ``from_dict`` classmethod if it needs custom parsing),
    write its ``_xxx_section()`` method, and add one entry here. attr is both
    the BenchConfig field name and the TOML table name. write returns None to
    omit the section from output entirely (for config that's only written
    when actually used, like s3 or waf).
    """

    attr: str
    read: Callable[[dict], Any]
    write: Callable[[BenchConfig], Any | None]


_SECTIONS: tuple[_Section, ...] = (
    _Section(
        "mariadb",
        lambda data: MariaDBConfig(**BenchConfig._known_fields(MariaDBConfig, data.get("mariadb", {}))),
        lambda config: config._mariadb_section(),
    ),
    _Section(
        "postgres",
        lambda data: PostgresConfig(**BenchConfig._known_fields(PostgresConfig, data.get("postgres", {}))),
        lambda config: config._postgres_section(),
    ),
    _Section(
        "redis",
        lambda data: RedisConfig.from_dict(data.get("redis", {})),
        lambda config: config._redis_section(),
    ),
    _Section(
        "workers",
        lambda data: WorkerConfig.from_dict(data.get("workers", [])),
        lambda config: config._workers_section(),
    ),
    _Section(
        "production",
        lambda data: ProductionConfig.from_dict(data.get("production")),
        lambda config: config._production_section(),
    ),
    _Section(
        "gunicorn",
        lambda data: GunicornConfig.from_dict(data.get("gunicorn", {})),
        lambda config: config._gunicorn_section(),
    ),
    _Section(
        "letsencrypt",
        lambda data: LetsEncryptConfig.from_dict(data.get("letsencrypt", {})),
        lambda config: config._letsencrypt_section(),
    ),
    _Section(
        "admin",
        lambda data: AdminConfig.from_dict(data.get("admin", {})),
        lambda config: config._admin_section(),
    ),
    _Section(
        "central",
        lambda data: CentralConfig.from_dict(data.get("central", {})),
        lambda config: config._central_section()
        if (config.central.endpoint or config.central.auth_token)
        else None,
    ),
    _Section(
        "firewall",
        lambda data: FirewallConfig.from_dict(data.get("firewall")),
        lambda config: config._firewall_section() if (config.firewall.enabled or config.firewall.rules) else None,
    ),
    _Section(
        "waf",
        lambda data: WafConfig.from_dict(data.get("waf")),
        lambda config: config._waf_section() if config.waf != WafConfig() else None,
    ),
    _Section(
        "s3",
        lambda data: S3Config(**BenchConfig._known_fields(S3Config, data.get("s3", {}))),
        lambda config: config._s3_section()
        if (config.s3.access_key or config.s3.secret_key or config.s3.bucket or config.s3.provider or config.s3.region)
        else None,
    ),
    _Section(
        "monitor",
        lambda data: MonitorConfig.from_dict(data.get("monitor", {})),
        lambda config: config._monitor_section() if config.production.enabled else None,
    ),
)


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


@dataclass
class _Table:
    """Declared shape of one bench.toml table: its accepted leaf keys plus any
    nested tables and arrays-of-tables. Drives unknown-field detection."""

    keys: set[str] = field(default_factory=set)
    tables: dict[str, "_Table"] = field(default_factory=dict)
    arrays: dict[str, "_Table"] = field(default_factory=dict)


def _keys(dataclass_type: type) -> set[str]:
    return {f.name for f in fields(dataclass_type)}


# The [bench] table flattens top-level fields whose keys differ from the
# BenchConfig attribute names (python vs python_version), so it is listed here.
_BENCH_KEYS = {
    "name",
    "python",
    "http_port",
    "socketio_port",
    "socketio_backend",
    "watch_apps_js",
    "reload_python",
    "watch_admin_js",
    "db_type",
    "default_branch",
}
# Keys older bench-cli versions wrote that the parser still tolerates.
_PRODUCTION_LEGACY = {"lightweight", "nginx"}
_WORKER_LEGACY = {"queue"}


def _bench_schema() -> _Table:
    return _Table(
        tables={
            "bench": _Table(keys=set(_BENCH_KEYS)),
            "mariadb": _Table(keys=_keys(MariaDBConfig)),
            "postgres": _Table(keys=_keys(PostgresConfig)),
            "redis": _Table(keys=_keys(RedisConfig)),
            "production": _Table(keys=_keys(ProductionConfig) | _PRODUCTION_LEGACY),
            "monitor": _Table(keys=_keys(MonitorConfig)),
            "nginx": _Table(keys=_keys(NginxConfig)),
            "gunicorn": _Table(keys=_keys(GunicornConfig)),
            "letsencrypt": _Table(keys=_keys(LetsEncryptConfig)),
            "admin": _Table(keys=_keys(AdminConfig)),
            "central": _Table(keys=_keys(CentralConfig)),
            "s3": _Table(keys=_keys(S3Config)),
            "firewall": _Table(
                keys=_keys(FirewallConfig) - {"rules"},
                arrays={"rules": _Table(keys=_keys(FirewallRule))},
            ),
            "waf": _Table(
                keys=_keys(WafConfig) - {"custom_rules"},
                arrays={
                    "custom_rules": _Table(
                        keys=_keys(WafRule) - {"conditions"},
                        arrays={"conditions": _Table(keys=_keys(WafCondition))},
                    )
                },
            ),
        },
        arrays={
            "apps": _Table(keys=_keys(AppConfig)),
            "workers": _Table(keys=_keys(WorkerGroup) | _WORKER_LEGACY),
        },
    )


_SCHEMA_ROOT = _bench_schema()


def _preserve_unknown(original: Mapping, replacement: Mapping, table: _Table) -> dict:
    result = copy.deepcopy(dict(replacement))
    for key, value in original.items():
        if key in table.tables and isinstance(value, Mapping):
            current = result.get(key, {})
            if isinstance(current, Mapping):
                result[key] = _preserve_unknown(value, current, table.tables[key])
        elif key in table.arrays and isinstance(value, list):
            current = result.get(key, [])
            if isinstance(current, list):
                result[key] = _preserve_unknown_array(value, current, table.arrays[key])
        elif key not in table.keys and key not in table.tables and key not in table.arrays:
            result[key] = copy.deepcopy(value)
    return result


def _preserve_unknown_array(original: list, replacement: list, table: _Table) -> list:
    result = copy.deepcopy(replacement)
    for index, (old_entry, new_entry) in enumerate(zip(original, result, strict=False)):
        if isinstance(old_entry, Mapping) and isinstance(new_entry, Mapping):
            result[index] = _preserve_unknown(old_entry, new_entry, table)
    return result


def _scan(data: Mapping, table: _Table, prefix: str) -> list[str]:
    unknown: list[str] = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if key in table.tables and isinstance(value, Mapping):
            unknown += _scan(value, table.tables[key], f"{path}.")
        elif key in table.arrays and isinstance(value, list):
            unknown += _scan_array(value, table.arrays[key], path)
        elif key not in table.keys and key not in table.tables and key not in table.arrays:
            unknown.append(path)
    return unknown


def _scan_array(entries: list, table: _Table, path: str) -> list[str]:
    unknown: list[str] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            unknown += _scan(entry, table, f"{path}[{index}].")
    return unknown
