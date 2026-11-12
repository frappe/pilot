from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.config.common_site_config import read_common_site_config, update_common_site_config
from pilot.config.mail import CONFIG_KEYS as MAIL_CONFIG_KEYS
from pilot.config.worker import FRAPPE_QUEUES
from pilot.core.site.config.public import PublicConfig
from pilot.exceptions import BenchError, MalformedSiteConfig

if TYPE_CHECKING:
    from pilot.config import S3Config
    from pilot.core.bench import Bench

# One process holds one client cache, so lite mode gives it a ceiling.
_LITE_CLIENT_CACHE_BYTES = 10 * 1024 * 1024

# Keys Pilot writes from bench.toml or other settings; an edit would be overwritten.
MANAGED_COMMON_SITE_CONFIG_KEYS = frozenset(
    {
        "client_cache_max_bytes",
        "default_site",
        "dns_multitenant",
        "maintenance_mode",
        "monitor",
        "redis_cache",
        "redis_queue",
        "redis_socketio",
        "s3_access_key",
        "s3_bucket",
        "s3_provider",
        "s3_region",
        "s3_secret_key",
        "socketio_backend",
        "socketio_port",
        "webserver_host",
        "webserver_port",
        "workers",
        *MAIL_CONFIG_KEYS,
    }
)


class BenchConfigFiles:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def get_db_root_args(self, db_type: str) -> list[str]:
        """The bench new-site arguments for the database root user."""
        if db_type == "postgres":
            postgres = self.bench.config.postgres
            return [
                "--db-root-username",
                postgres.admin_user,
                "--db-root-password",
                self.postgres_root_password,
            ]
        if db_type == "sqlite":
            return []
        mariadb = self.bench.config.mariadb
        return [
            "--db-root-username",
            mariadb.admin_user,
            "--db-root-password",
            mariadb.root_password,
        ]

    @property
    def postgres_root_password(self) -> str:
        """The PostgreSQL root password, or the trust placeholder when none is set."""
        return self.bench.config.postgres.root_password or "trust_auth"

    def set_maintenance_mode(self, enabled: bool) -> None:
        """Turn maintenance mode on or off for every site."""
        with update_common_site_config(self.bench.sites_path) as config:
            config["maintenance_mode"] = 1 if enabled else 0

    def sync_s3_credentials(self, s3_config: "S3Config") -> None:
        """Write the backup storage credentials to common_site_config.json."""
        if not (self.bench.sites_path / "common_site_config.json").exists():
            return

        with update_common_site_config(self.bench.sites_path) as config:
            config["s3_access_key"] = s3_config.access_key
            config["s3_bucket"] = s3_config.bucket
            config["s3_secret_key"] = s3_config.secret_key
            config["s3_provider"] = s3_config.provider
            config["s3_region"] = s3_config.region

    @property
    def public_common_site_config(self) -> dict:
        """common_site_config.json with secrets masked and without Pilot-managed keys."""
        return _editable(read_common_site_config(self.bench.sites_path))

    def update_public_common_site_config(self, patch: dict) -> dict:
        """Apply a patch to the editable common config; refuse keys Pilot manages."""
        if managed := sorted(set(patch) & MANAGED_COMMON_SITE_CONFIG_KEYS):
            raise BenchError(f"Pilot manages {', '.join(managed)}; change it through its own setting.")
        with update_common_site_config(self.bench.sites_path) as config:
            public = PublicConfig(config)
            if error := public.get_patch_error(patch):
                raise BenchError(error)
            merged = public.merge(patch)
            config.clear()
            config.update(merged)
        return _editable(merged)

    def write_common_site_config(self) -> None:
        """Write the Redis URLs, ports and other managed keys to common_site_config.json."""
        redis = self.bench.config.redis
        redis_cache = f"redis://localhost:{redis.cache_port}"
        with update_common_site_config(self.bench.sites_path) as config:
            config.update(
                {
                    "redis_cache": redis_cache,
                    "redis_queue": f"redis://localhost:{redis.queue_port}",
                    "redis_socketio": redis_cache,
                    "socketio_port": self.bench.realtime_port,
                    "webserver_port": self.bench.config.http_port,
                    # Realtime checks sessions against web here, not through nginx and the site's TLS.
                    "webserver_host": "127.0.0.1",
                    "socketio_backend": self.bench.config.socketio_backend,
                    "monitor": True,
                }
            )
            if self.bench.is_lite_mode:
                config["client_cache_max_bytes"] = _LITE_CLIENT_CACHE_BYTES
            else:
                config.pop("client_cache_max_bytes", None)
            self.set_worker_queues(config)

    def set_worker_queues(self, config: dict) -> None:
        """Frappe rejects a queue missing from `workers` and reads each queue's job timeout there.
        Queues no worker group lists are left alone."""
        entries = config.get("workers", {})
        if not isinstance(entries, dict):
            raise MalformedSiteConfig("common_site_config.json `workers` must be an object, fix it by hand.")
        workers = self.bench.config.workers
        timeouts = workers.queue_timeouts
        for queue in workers.queues:
            if queue in timeouts:
                entries[queue] = {"timeout": timeouts[queue]}
            elif queue in FRAPPE_QUEUES:
                entries.pop(queue, None)
            else:
                entries[queue] = {}
        if entries:
            config["workers"] = entries
        else:
            config.pop("workers", None)


def _editable(config: dict) -> dict:
    """The config keys a user may see and edit."""
    return {
        key: value
        for key, value in PublicConfig(config).visible.items()
        if key not in MANAGED_COMMON_SITE_CONFIG_KEYS
    }
