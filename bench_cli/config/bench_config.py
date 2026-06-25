import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from bench_cli.config.admin_config import AdminConfig
from bench_cli.config.app_config import AppConfig
from bench_cli.config.gunicorn_config import GunicornConfig
from bench_cli.config.letsencrypt_config import LetsEncryptConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.nginx_config import NginxConfig
from bench_cli.config.production_config import ProductionConfig
from bench_cli.config.redis_config import RedisConfig
from bench_cli.config.volume_config import DatasetConfig, ImageConfig, VolumeConfig
from bench_cli.config.worker_config import WorkerConfig, WorkerGroup
from bench_cli.exceptions import ConfigError

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
_ZFS_SIZE_PATTERN = re.compile(r"^[1-9]\d*[KMGTkmgt]?$")
# Lenient hostname: dotted labels of alphanumerics/hyphens. Allows dev names
# like "admin1.localhost" and real domains like "admin.example.com".
_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
_REDIS_PORT_MIN = 1024
_REDIS_PORT_MAX = 65535
_PORT_MIN = 1
_PORT_MAX = 65535


@dataclass
class BenchConfig:
    name: str
    python_version: str
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    apps: List[AppConfig] = field(default_factory=list)
    http_port: int = 8000
    socketio_port: int = 9000
    socketio_backend: str = "node"
    default_branch: str = ""
    production: ProductionConfig = field(default_factory=ProductionConfig)
    nginx: NginxConfig = field(default_factory=NginxConfig)
    gunicorn: GunicornConfig = field(default_factory=GunicornConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)

    @classmethod
    def from_file(cls, path: Path) -> "BenchConfig":
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        config = cls._from_dict(data)
        config.validate()
        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "BenchConfig":
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
        mariadb = MariaDBConfig(**data.get("mariadb", {}))
        redis = cls._parse_redis(data.get("redis", {}))
        workers = cls._parse_workers(data.get("workers", []))
        production = cls._parse_production(data.get("production"))
        gunicorn = cls._parse_gunicorn(data.get("gunicorn", {}), bench_data.get("http_port", 8000))
        letsencrypt = cls._parse_letsencrypt(data.get("letsencrypt", {}))
        admin = cls._parse_admin(data.get("admin", {}))
        volume = cls._parse_volume(data.get("volume"))
        # One dataset per bench, named after the bench unless explicitly set.
        if not volume.name:
            volume.name = bench_data.get("name", "")
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            socketio_backend=bench_data.get("socketio_backend", "node"),
            default_branch=bench_data.get("default_branch", ""),
            apps=apps,
            mariadb=mariadb,
            redis=redis,
            workers=workers,
            production=production,
            gunicorn=gunicorn,
            letsencrypt=letsencrypt,
            admin=admin,
            volume=volume,
        )

    @staticmethod
    def _parse_redis(data: dict) -> RedisConfig:
        return RedisConfig(
            cache_port=data.get("cache_port", 13000),
            queue_port=data.get("queue_port", 11000),
            version=data.get("version"),
        )

    @staticmethod
    def _parse_workers(data: list) -> WorkerConfig:
        # [[workers]] array-of-tables: each group lists queues and a count.
        if not isinstance(data, list) or not data:
            return WorkerConfig()
        groups = [
            WorkerGroup(
                queues=entry.get("queues", [entry.get("queue", "default")]),
                count=entry.get("count", 1),
            )
            for entry in data
        ]
        return WorkerConfig(groups=groups)

    @staticmethod
    def _normalize_process_manager(value: str) -> str:
        v = (value or "").strip().lower()
        if v in ("", "none"):
            return ""
        if v == "supervisord":
            return "supervisor"
        return v

    @staticmethod
    def _parse_production(data: dict | None) -> ProductionConfig:
        if data is None:
            return ProductionConfig()
        pm = BenchConfig._normalize_process_manager(str(data.get("process_manager", "")))
        if "enabled" in data:
            enabled = bool(data.get("enabled"))
        else:
            # Legacy: presence of a real process_manager implied production.
            enabled = pm != ""
        # Oldest format derived the manager from a `lightweight` flag.
        if enabled and not pm and "lightweight" in data:
            pm = "systemd" if data.get("lightweight", False) else "supervisor"
        return ProductionConfig(
            enabled=enabled,
            process_manager=pm,
            use_companion_manager=data.get("use_companion_manager", False),
        )

    @staticmethod
    def _parse_gunicorn(data: dict, http_port: int = 8000) -> GunicornConfig:
        d = GunicornConfig()
        return GunicornConfig(
            workers=data.get("workers", d.workers),
            threads=data.get("threads", d.threads),
            timeout=data.get("timeout", d.timeout),
            worker_class=data.get("worker_class", d.worker_class),
            malloc_arena_max=data.get("malloc_arena_max", d.malloc_arena_max),
            max_requests=data.get("max_requests", d.max_requests),
            max_requests_jitter=data.get("max_requests_jitter", d.max_requests_jitter),
        )

    @staticmethod
    def _parse_letsencrypt(data: dict) -> LetsEncryptConfig:
        webroot_path = data.get("webroot_path", "/var/www/letsencrypt")
        return LetsEncryptConfig(
            email=data.get("email", ""),
            webroot_path=Path(webroot_path),
        )

    @staticmethod
    def _parse_admin(data: dict) -> AdminConfig:
        return AdminConfig(
            port=data.get("port", 7000),
            timeout=data.get("timeout", 180),
            enabled=data.get("enabled", False),
            password=data.get("password", ""),
            jwt_secret=data.get("jwt_secret", ""),
            domain=data.get("domain", ""),
            tls=data.get("tls", False),
        )

    @staticmethod
    def _parse_volume(data: dict | None) -> VolumeConfig:
        if data is None:
            return VolumeConfig()
        dataset_data = data.get("dataset", {})
        image_data = data.get("image", {})
        # Older tomls predate `backing`: an explicit device implies device backing.
        backing = data.get("backing") or ("device" if data.get("device") else "auto")
        return VolumeConfig(
            enabled=data.get("enabled", True),
            pool=data.get("pool", "bench-pool"),
            name=data.get("name", ""),
            backing=backing,
            device=data.get("device", ""),
            image=ImageConfig(
                size=image_data.get("size", ""),
                path=image_data.get("path", ""),
            ),
            dataset=DatasetConfig(
                reservation=dataset_data.get("reservation", "5G"),
                quota=dataset_data.get("quota", "50G"),
            ),
        )

    def validate(self) -> None:
        self._validate_required_fields()
        self._validate_bench_name()
        self._validate_app_names_unique()
        self._validate_ports()
        self._validate_socketio_backend()
        self._validate_redis_ports()
        self._validate_worker_counts()
        self._validate_letsencrypt_email()
        self._validate_gunicorn()
        self._validate_mariadb_version()
        self._validate_mariadb_instance()
        self._validate_redis_version()
        self._validate_production()
        self._validate_admin_domain()
        if self.volume.enabled:
            self._validate_volume()

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
            raise ConfigError(f"bench.name '{self.name}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens.")

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
        }
        for name, port in ports.items():
            if not (_PORT_MIN <= port <= _PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}.")

    def _validate_socketio_backend(self) -> None:
        if self.socketio_backend not in ("python", "node"):
            raise ConfigError(f"bench.socketio_backend '{self.socketio_backend}' is invalid. Must be 'python' or 'node'.")

    def _validate_redis_ports(self) -> None:
        ports = [self.redis.cache_port, self.redis.queue_port]
        port_names = ["redis.cache_port", "redis.queue_port"]
        for name, port in zip(port_names, ports):
            if not (_REDIS_PORT_MIN <= port <= _REDIS_PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_REDIS_PORT_MIN} and {_REDIS_PORT_MAX}.")

        if self.redis.cache_port == self.redis.queue_port:
            raise ConfigError(f"redis.cache_port and redis.queue_port must be distinct, but both are set to {self.redis.cache_port}.")

    def _validate_worker_counts(self) -> None:
        if not self.workers.groups:
            raise ConfigError("workers.groups must contain at least one worker group.")
        for i, group in enumerate(self.workers.groups):
            prefix = f"workers[{i}]"
            if not isinstance(group.queues, list) or not group.queues:
                raise ConfigError(f"{prefix}.queues must be a non-empty list.")
            if not all(isinstance(q, str) and q for q in group.queues):
                raise ConfigError(f"{prefix}.queues must contain non-empty strings.")
            if not isinstance(group.count, int) or group.count < 1:
                raise ConfigError(f"{prefix}.count must be a positive integer, got '{group.count}'.")

    def _validate_letsencrypt_email(self) -> None:
        if self.letsencrypt.email and not _EMAIL_PATTERN.match(self.letsencrypt.email):
            raise ConfigError(f"letsencrypt.email '{self.letsencrypt.email}' is not a valid email address.")

    def _validate_production(self) -> None:
        from bench_cli.config.production_config import VALID_PROCESS_MANAGERS

        pm = self.production.process_manager
        if self.production.enabled:
            if pm not in VALID_PROCESS_MANAGERS:
                raise ConfigError(
                    f"production.process_manager must be one of {', '.join(VALID_PROCESS_MANAGERS)} "
                    f"when production is enabled (bench '{self.name}'), got '{pm or '(empty)'}'."
                )
        elif pm and pm not in VALID_PROCESS_MANAGERS:
            raise ConfigError(
                f"production.process_manager '{pm}' is invalid (bench '{self.name}'). "
                f"Must be one of {', '.join(VALID_PROCESS_MANAGERS)}."
            )

    def _validate_admin_domain(self) -> None:
        domain = self.admin.domain
        if not domain:
            if self.production.enabled:
                raise ConfigError(
                    f"admin.domain is required in production but is missing for bench '{self.name}'. "
                    f"Set it in bench.toml (e.g. admin.example.com) or pass "
                    f"'bench setup production --admin-domain <domain>'."
                )
            return
        if not _HOSTNAME_PATTERN.match(domain):
            raise ConfigError(f"admin.domain '{domain}' is not a valid hostname (bench '{self.name}').")

    def _validate_gunicorn(self) -> None:
        if not isinstance(self.gunicorn.workers, int) or self.gunicorn.workers < 1:
            raise ConfigError(f"gunicorn.workers must be a positive integer, got '{self.gunicorn.workers}'.")
        if not isinstance(self.gunicorn.threads, int) or self.gunicorn.threads < 1:
            raise ConfigError(f"gunicorn.threads must be a positive integer, got '{self.gunicorn.threads}'.")
        if not isinstance(self.gunicorn.timeout, int) or self.gunicorn.timeout < 1:
            raise ConfigError(f"gunicorn.timeout must be a positive integer, got '{self.gunicorn.timeout}'.")
        if not self.gunicorn.worker_class:
            raise ConfigError("gunicorn.worker_class must not be empty.")
        if not isinstance(self.gunicorn.malloc_arena_max, int) or self.gunicorn.malloc_arena_max < 0:
            raise ConfigError(
                f"gunicorn.malloc_arena_max must be a non-negative integer, got '{self.gunicorn.malloc_arena_max}'."
            )
        if not isinstance(self.gunicorn.max_requests, int) or self.gunicorn.max_requests < 0:
            raise ConfigError(
                f"gunicorn.max_requests must be a non-negative integer, got '{self.gunicorn.max_requests}'."
            )
        if not isinstance(self.gunicorn.max_requests_jitter, int) or self.gunicorn.max_requests_jitter < 0:
            raise ConfigError(
                f"gunicorn.max_requests_jitter must be a non-negative integer, got '{self.gunicorn.max_requests_jitter}'."
            )

    def _validate_mariadb_version(self) -> None:
        if self.mariadb.version and not _VERSION_PATTERN.match(self.mariadb.version):
            raise ConfigError(f"mariadb.version '{self.mariadb.version}' is invalid. Must be a version string like '11.8' or '11.4'.")

    def _validate_mariadb_instance(self) -> None:
        instance = self.mariadb.instance
        if instance and not _BENCH_NAME_PATTERN.match(instance):
            raise ConfigError(
                f"mariadb.instance '{instance}' is invalid. Must start with a letter and contain only "
                "letters, digits, underscores, or hyphens."
            )
        if self.mariadb.data_dir and not Path(self.mariadb.data_dir).is_absolute():
            raise ConfigError(f"mariadb.data_dir '{self.mariadb.data_dir}' must be an absolute path.")

    def _validate_redis_version(self) -> None:
        if self.redis.version and not _VERSION_PATTERN.match(self.redis.version):
            raise ConfigError(f"redis.version '{self.redis.version}' is invalid. Must be a version string like '7' or '7.0'.")

    def _validate_volume(self) -> None:
        if not self.volume.enabled:
            return
        if not self.volume.pool:
            raise ConfigError("volume.pool is required.")
        self._validate_volume_backing()
        self._validate_zfs_size("volume.dataset.reservation", self.volume.dataset.reservation)
        self._validate_zfs_size("volume.dataset.quota", self.volume.dataset.quota)
        self._validate_reservation_quota()

    def _validate_volume_backing(self) -> None:
        backing = self.volume.backing
        if backing not in ("device", "image", "auto"):
            raise ConfigError(f"volume.backing '{backing}' is invalid. Must be 'device', 'image', or 'auto'.")
        if backing == "auto":
            # Resolved to a concrete backing (with smart sizes) during bench init.
            return
        if backing == "device":
            if not self.volume.device:
                raise ConfigError("volume.device is required when volume.backing = 'device'.")
            return
        if not self.volume.image.size:
            raise ConfigError("volume.image.size is required when volume.backing = 'image'.")
        self._validate_zfs_size("volume.image.size", self.volume.image.size)
        if self.volume.image.path and not Path(self.volume.image.path).is_absolute():
            raise ConfigError(f"volume.image.path '{self.volume.image.path}' must be an absolute path.")

    def _validate_reservation_quota(self) -> None:
        from bench_cli.managers.volume_manager import VolumeManager

        dataset = self.volume.dataset
        if error := VolumeManager.validate_reservation_within_quota(dataset.reservation, dataset.quota):
            raise ConfigError(error)

    @staticmethod
    def _validate_zfs_size(field_name: str, value: str) -> None:
        if not _ZFS_SIZE_PATTERN.match(value):
            raise ConfigError(
                f"{field_name} '{value}' is not a valid ZFS size. Must be a positive integer with an optional K/M/G/T suffix — examples: '10G', '512M', '1T'. Decimals and negatives are not allowed."
            )

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
