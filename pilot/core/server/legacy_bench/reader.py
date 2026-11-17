from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, CommandError
from pilot.internal.command import run_command
from pilot.internal.git import GitRepo

if TYPE_CHECKING:
    from pilot.core.server.legacy_bench.database import DatabaseServer

FRAMEWORK_APP = "frappe"
DEFAULT_DATABASE_PORT = 3306


@dataclass
class LegacyApp:
    """An app checkout in a bench made by the `bench` CLI."""

    name: str
    path: Path

    @cached_property
    def remote_url(self) -> str:
        """The upstream remote, which the `bench` CLI names `upstream`, else origin."""
        for remote in ("upstream", "origin"):
            try:
                result = run_command(["git", "-C", str(self.path), "remote", "get-url", remote], timeout=10)
                return result.stdout.decode().strip()
            except CommandError:
                continue
        return ""

    @property
    def repo(self) -> str:
        from pilot.integrations.git.base import without_credentials

        return without_credentials(self.remote_url)

    @property
    def has_credentials_in_remote(self) -> bool:
        return self.repo != self.remote_url

    @cached_property
    def branch(self) -> str:
        """The checked-out branch, or '' when the checkout is detached."""
        return GitRepo(self.path).branch

    @cached_property
    def commit(self) -> str:
        return GitRepo(self.path).short_head

    @cached_property
    def has_local_changes(self) -> bool:
        return GitRepo(self.path).has_local_changes

    @property
    def revision(self) -> str:
        return f"{self.branch or 'detached'} @ {self.commit}"


@dataclass
class LegacySite:
    """A site directory in a bench made by the `bench` CLI."""

    name: str
    path: Path
    common_config: dict

    @cached_property
    def config(self) -> dict:
        return json.loads((self.path / "site_config.json").read_text())

    @property
    def db_type(self) -> str:
        return self.config.get("db_type") or "mariadb"

    @property
    def installed_apps(self) -> list[str]:
        return list(self.config.get("installed_apps") or [])

    def get_config_value(self, key: str, default=None):
        """A site_config.json value, falling back to common_site_config.json."""
        return self.config.get(key) or self.common_config.get(key) or default

    @property
    def database_server(self) -> "DatabaseServer":
        from pilot.core.server.legacy_bench.database import DatabaseServer

        return DatabaseServer(
            str(self.get_config_value("db_host", "localhost")),
            int(self.get_config_value("db_port", DEFAULT_DATABASE_PORT)),
            str(self.get_config_value("db_socket", "")),
        )

    @property
    def database_user(self) -> str:
        return self.config.get("db_user") or self.config["db_name"]


class LegacyBench:
    """A bench made by the `bench` CLI, read without changing it.

    Exposes `apps_path`, `staging_path` and `env_path` like `Bench`, so the app
    validator's source checks can run on its apps before anything is created."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not (self.apps_path / FRAMEWORK_APP).is_dir() or not self.sites_path.is_dir():
            raise BenchError(f"'{self.path}' is not a bench: it needs apps/{FRAMEWORK_APP} and sites/.")
        if (self.path / "bench.toml").exists():
            raise BenchError(f"'{self.path}' is already a Pilot bench.")

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def apps_path(self) -> Path:
        return self.path / "apps"

    @property
    def sites_path(self) -> Path:
        return self.path / "sites"

    @property
    def staging_path(self) -> Path:
        return self.apps_path

    @property
    def env_path(self) -> Path:
        return self.path / "env"

    @property
    def python(self) -> Path:
        return self.env_path / "bin" / "python"

    @cached_property
    def common_site_config(self) -> dict:
        path = self.sites_path / "common_site_config.json"
        return json.loads(path.read_text()) if path.exists() else {}

    @property
    def database_server(self) -> "DatabaseServer":
        """The server sites use unless their own config names another."""
        from pilot.core.server.legacy_bench.database import DatabaseServer

        config = self.common_site_config
        return DatabaseServer(
            str(config.get("db_host") or "localhost"),
            int(config.get("db_port") or DEFAULT_DATABASE_PORT),
            str(config.get("db_socket") or ""),
        )

    @property
    def registered_apps(self) -> list[str]:
        apps_txt = self.sites_path / "apps.txt"
        return (
            [line.strip() for line in apps_txt.read_text().splitlines() if line.strip()]
            if apps_txt.exists()
            else []
        )

    @cached_property
    def sites(self) -> list[LegacySite]:
        return [
            LegacySite(path.name, path, self.common_site_config)
            for path in sorted(self.sites_path.iterdir())
            if path.is_dir() and not path.is_symlink() and (path / "site_config.json").exists()
        ]

    @property
    def framework_app(self) -> LegacyApp:
        return LegacyApp(FRAMEWORK_APP, self.apps_path / FRAMEWORK_APP)

    @cached_property
    def apps(self) -> list[LegacyApp]:
        """The apps to import, in install order: the bench's apps.txt, then any other
        app a site uses. The framework app comes first."""
        names = [FRAMEWORK_APP, *self.registered_apps]
        names += [app for site in self.sites for app in site.installed_apps]
        ordered = list(dict.fromkeys(names))
        return [
            LegacyApp(name, self.apps_path / name) for name in ordered if (self.apps_path / name).is_dir()
        ]

    @property
    def skipped_apps(self) -> list[str]:
        """Checkouts in apps/ that neither the bench nor any site uses."""
        imported = {app.name for app in self.apps}
        return sorted(
            path.name
            for path in self.apps_path.iterdir()
            if path.is_dir() and (path / ".git").exists() and path.name not in imported
        )

    @property
    def missing_apps(self) -> list[str]:
        """Apps a site lists as installed that have no checkout in apps/."""
        names = {app for site in self.sites for app in site.installed_apps}
        return sorted(name for name in names if not (self.apps_path / name).is_dir())
