import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List

from pilot.config.admin import AdminConfig
from pilot.config.app import AppConfig
from pilot.config.central import CentralConfig
from pilot.config.config_schema import unknown_config_paths
from pilot.config.firewall import FirewallConfig
from pilot.config.gunicorn import GunicornConfig
from pilot.config.letsencrypt import LetsEncryptConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.config.monitor import MonitorConfig
from pilot.config.nginx import NginxConfig
from pilot.config.postgres import PostgresConfig
from pilot.config.production import ProductionConfig
from pilot.config.redis import RedisConfig
from pilot.config.s3 import S3Config
from pilot.config.waf import WafConfig
from pilot.config.worker import WorkerConfig
from pilot.exceptions import ConfigError

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_PORT_MIN = 1
_PORT_MAX = 65535


@dataclass
class BenchConfig:
    name: str
    python_version: str
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    apps: List[AppConfig] = field(default_factory=list)
    http_port: int = 8000
    socketio_port: int = 9000
    socketio_backend: str = "node"
    watch_apps_js: bool = False
    reload_python: bool = False
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

    @classmethod
    def from_file(cls, path: Path) -> "BenchConfig":
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        config = cls._from_dict(data)
        config.validate()
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
        mariadb = MariaDBConfig(**cls._known_fields(MariaDBConfig, data.get("mariadb", {})))
        postgres = PostgresConfig(**cls._known_fields(PostgresConfig, data.get("postgres", {})))
        redis = RedisConfig.from_dict(data.get("redis", {}))
        workers = WorkerConfig.from_dict(data.get("workers", []))
        production = ProductionConfig.from_dict(data.get("production"))
        monitor = MonitorConfig.from_dict(data.get("monitor", {}))
        gunicorn = GunicornConfig.from_dict(data.get("gunicorn", {}))
        letsencrypt = LetsEncryptConfig.from_dict(data.get("letsencrypt", {}))
        admin = AdminConfig.from_dict(data.get("admin", {}))
        central = CentralConfig.from_dict(data.get("central", {}))
        firewall = FirewallConfig.from_dict(data.get("firewall"))
        waf = WafConfig.from_dict(data.get("waf"))
        s3 = S3Config(**cls._known_fields(S3Config, data.get("s3", {})))
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            socketio_backend=bench_data.get("socketio_backend", "node"),
            watch_apps_js=bench_data.get("watch_apps_js", False),
            reload_python=bench_data.get("reload_python", False),
            watch_admin_js=bench_data.get("watch_admin_js", False),
            db_type=bench_data.get("db_type", "mariadb"),
            default_branch=bench_data.get("default_branch", ""),
            apps=apps,
            mariadb=mariadb,
            postgres=postgres,
            redis=redis,
            workers=workers,
            production=production,
            monitor=monitor,
            gunicorn=gunicorn,
            letsencrypt=letsencrypt,
            admin=admin,
            central=central,
            firewall=firewall,
            waf=waf,
            s3=s3,
        )

    @staticmethod
    def _known_fields(dataclass_type: type, data: dict) -> dict:
        """Drop keys a bench.toml table has that the dataclass no longer
        declares, so a config written by an older bench-cli still loads."""
        known = {f.name for f in fields(dataclass_type)}
        return {k: v for k, v in data.items() if k in known}

    @staticmethod
    def _report_unknown_fields(data: dict, *, strict: bool) -> None:
        """Unknown keys are ignored so older/foreign configs still load; strict
        (opt-in, for validation) raises ConfigError naming them."""
        if not strict:
            return
        paths = unknown_config_paths(data)
        if paths:
            raise ConfigError(f"bench.toml has unrecognized fields: {', '.join(paths)}")

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
                raise ConfigError(f"App '{app.name}': active branch '{app.branch}' is not listed in branches {app.branches}.")

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
                raise ConfigError(f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}.")

    def _validate_socketio_backend(self) -> None:
        if self.socketio_backend not in ("python", "node"):
            raise ConfigError(f"bench.socketio_backend '{self.socketio_backend}' is invalid. Must be 'python' or 'node'.")

    def _validate_db_type(self) -> None:
        if self.db_type not in ("mariadb", "postgres", "sqlite"):
            raise ConfigError(f"bench.db_type '{self.db_type}' is invalid. Must be 'mariadb', 'postgres', or 'sqlite'.")

    @property
    def framework_app(self) -> AppConfig:
        if not self.apps:
            return AppConfig(name="frappe", repo="", branch="")
        return self.apps[0]

    def app_by_name(self, name: str) -> AppConfig:
        for app in self.apps:
            if app.name == name:
                return app
        raise KeyError(f"No app named '{name}' found in config.")
