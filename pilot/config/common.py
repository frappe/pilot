from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

from pilot.config.alert_limit import ResourceLimitConfig
from pilot.config.central import CentralConfig
from pilot.config.datum import DatumConfig
from pilot.config.letsencrypt import LetsEncryptConfig
from pilot.config.logs import LogsConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.config.postgres import PostgresConfig
from pilot.config.proxy import ProxyConfig
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.toml import ConfigDict, Toml

FILENAME = "common_config.toml"


@dataclass
class CommonConfig:
    """Settings shared by every bench in one benches directory."""

    mariadb: MariaDBConfig = field(default_factory=MariaDBConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    central: CentralConfig = field(default_factory=CentralConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    datum: DatumConfig = field(default_factory=DatumConfig)
    logs: LogsConfig = field(default_factory=LogsConfig)
    resource_limits: ResourceLimitConfig = field(default_factory=ResourceLimitConfig)
    jwks_url: str = ""
    jwks_audience: str = ""

    @classmethod
    def path(cls, benches_root: Path) -> Path:
        return Path(benches_root) / FILENAME

    @classmethod
    def read(cls, benches_root: Path) -> "CommonConfig":
        path = cls.path(benches_root)
        if not path.exists():
            return cls()
        return cls.from_raw_dict(Toml.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_raw_dict(cls, data: dict) -> "CommonConfig":
        """Build from a parsed TOML dict shaped like common_config.toml (or a
        bench.toml that still carries these tables pre-migration)."""
        admin = data.get("admin", {})
        return cls(
            mariadb=MariaDBConfig(**_known_fields(MariaDBConfig, data.get("mariadb", {}))),
            postgres=PostgresConfig(**_known_fields(PostgresConfig, data.get("postgres", {}))),
            letsencrypt=LetsEncryptConfig.from_dict(data.get("letsencrypt", {})),
            central=CentralConfig.from_dict(data.get("central", {})),
            proxy=ProxyConfig.from_dict(data.get("proxy", {})),
            datum=DatumConfig.from_dict(data.get("datum", {})),
            logs=LogsConfig.from_dict(data.get("logs", {})),
            resource_limits=ResourceLimitConfig(
                **_known_fields(ResourceLimitConfig, data.get("resource_limits", {}))
            ),
            jwks_url=admin.get("jwks_url", ""),
            jwks_audience=admin.get("jwks_audience", ""),
        )

    def write(self, benches_root: Path) -> None:
        path = self.path(benches_root)
        with exclusive_file_lock(path):
            replace_private_text_locked(path, Toml.dumps(self._to_toml_dict()))

    @classmethod
    @contextmanager
    def open(cls, benches_root: Path) -> Iterator["CommonConfig"]:
        """Lock common_config.toml for one read-modify-write transaction."""
        path = cls.path(benches_root)
        with exclusive_file_lock(path):
            config = cls.read(benches_root)
            original = copy.deepcopy(config)
            yield config
            if config != original:
                replace_private_text_locked(path, Toml.dumps(config._to_toml_dict()))

    @classmethod
    def apply_changes(
        cls,
        benches_root: Path,
        baseline: "CommonConfig | None",
        updated: "CommonConfig",
    ) -> None:
        """Commit only the settings that differ from `baseline`, under the lock.

        A caller holding a view read earlier must not write the whole file back:
        anything another bench committed since would be undone. Comparison goes
        down to individual settings, so changing one value in a table leaves the
        table's others as whoever committed them. Without a baseline there is
        nothing to compare, and the view is written as a whole.
        """
        if baseline is None:
            updated.write_if_changed(benches_root)
            return
        if baseline == updated:
            return
        with cls.open(benches_root) as current:
            _copy_changed_settings(baseline, updated, current)

    def write_if_changed(self, benches_root: Path) -> None:
        """Replace the shared file when this view differs from it."""
        path = self.path(benches_root)
        with exclusive_file_lock(path):
            if self != self.read(benches_root):
                replace_private_text_locked(path, Toml.dumps(self._to_toml_dict()))

    def _to_toml_dict(self) -> ConfigDict:
        data: ConfigDict = {
            "mariadb": {
                "host": self.mariadb.host,
                "port": self.mariadb.port,
                "root_password": self.mariadb.root_password,
                "admin_user": self.mariadb.admin_user,
                "socket_path": self.mariadb.socket_path,
                "existing": self.mariadb.existing,
            },
            "postgres": {
                "host": self.postgres.host,
                "port": self.postgres.port,
                "root_password": self.postgres.root_password,
                "admin_user": self.postgres.admin_user,
                "existing": self.postgres.existing,
            },
            "letsencrypt": {
                "email": self.letsencrypt.email,
                "webroot_path": str(self.letsencrypt.webroot_path),
            },
        }
        if self.central != CentralConfig():
            data["central"] = {
                "enabled": self.central.enabled,
                "bootstrapped": self.central.bootstrapped,
                "hostname_aliases": [
                    {
                        "type": alias.type,
                        "pattern": alias.pattern,
                        "target": alias.target,
                        "redirect": alias.redirect,
                    }
                    for alias in self.central.hostname_aliases
                ],
            }
        if self.proxy != ProxyConfig():
            data["proxy"] = {"protocol_v2": self.proxy.protocol_v2}
        if self.datum != DatumConfig():
            data["datum"] = {"endpoint": self.datum.endpoint, "token": self.datum.token}
        if self.logs != LogsConfig():
            data["logs"] = {
                "endpoint": self.logs.endpoint,
                "token": self.logs.token,
                "enabled": self.logs.enabled,
            }
        if self.resource_limits != ResourceLimitConfig():
            data["resource_limits"] = asdict(self.resource_limits)
        if self.jwks_url:
            data["admin"] = {"jwks_url": self.jwks_url, "jwks_audience": self.jwks_audience}
        return data


def _known_fields(dataclass_type: type, data: dict) -> dict:
    known = {f.name for f in fields(dataclass_type)}
    return {key: value for key, value in data.items() if key in known}


def _copy_changed_settings(baseline, updated, current) -> None:
    """Copy across the individual settings that changed, recursing into tables.

    Comparing whole tables would be enough to spot a change but not to apply
    one: writing the table back would carry this view's stale copy of every
    other setting in it with it.
    """
    for setting in fields(updated):
        was = getattr(baseline, setting.name)
        now = getattr(updated, setting.name)
        if was == now:
            continue
        if is_dataclass(was) and is_dataclass(now):
            _copy_changed_settings(was, now, getattr(current, setting.name))
        else:
            setattr(current, setting.name, now)
