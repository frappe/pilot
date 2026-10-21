from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.config import AppConfig
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class BenchInventory:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def app(self, name: str) -> "App":
        app_path = self.bench.apps_path / name
        if not app_path.is_dir():
            app_path = self.bench.apps_path / name.replace("_", "-")

        if not app_path.is_dir():
            raise BenchError(f"App {name} not found in bench")

        return self.get_app_at(app_path)

    def apps(self) -> list["App"]:
        if not self.bench.apps_path.is_dir():
            return []
        return [
            self.get_app_at(app_path)
            for app_path in sorted(self.bench.apps_path.iterdir())
            if app_path.is_dir() and (app_path / ".git").exists()
        ]

    def get_app_at(self, app_path: Path) -> "App":
        """The app cloned at `app_path`, with git state over bench.toml for its branch."""
        from pilot.config import AppConfig
        from pilot.core.app import App

        configured = self.get_configured_app(app_path.name)
        return App(
            AppConfig(
                name=app_path.name,
                repo=self._git_remote(app_path),
                branch=self._git_branch(app_path) or configured.branch,
                follow_branch=configured.follow_branch,
            ),
            self.bench,
        )

    def registered_apps(self) -> list[str]:
        apps_txt = self.bench.sites_path / "apps.txt"
        return apps_txt.read_text().splitlines() if apps_txt.exists() else []

    def is_app_installed(self, name: str) -> bool:
        from pilot.config import AppConfig
        from pilot.core.app import App

        module_name = App(AppConfig(name=name, repo="", branch=""), self.bench).module_name
        return module_name in self.registered_apps()

    def init_apps(self) -> list["App"]:
        from pilot.core.app import App

        return [App(app_config, self.bench) for app_config in self.bench.config.apps]

    def sites(self) -> list["Site"]:
        from pilot.core.site import Site

        if not self.bench.sites_path.is_dir():
            return []
        result = []
        for site_dir in sorted(self.bench.sites_path.iterdir()):
            config_path = site_dir / "site_config.json"
            # A symlink here is the compatibility path a rename leaves behind while
            # it swings over; counting it would render the same site twice.
            if site_dir.is_symlink():
                continue
            if site_dir.is_dir() and config_path.exists():
                raw = self._read_site_config(config_path)
                result.append(Site(self._site_config(site_dir.name, raw), self.bench))
        return result

    def write_apps_txt(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        names = [app.config.name for app in self.apps()]
        apps_txt.write_text("\n".join(names) + "\n" if names else "")

    def _site_config(self, name: str, raw: dict):
        from pilot.config import RoutePolicy, SiteConfig, SiteDomain

        domains = [SiteDomain.from_entry(entry) for entry in (raw.get("domains") or [])]
        domains = [domain for domain in domains if domain.name]
        host_name = (raw.get("host_name") or "").strip()
        primary = host_name.split("://", 1)[-1] if host_name else ""
        return SiteConfig(
            name=name,
            apps=[],
            ssl=bool(raw.get("ssl")),
            domains=domains,
            primary_domain=primary,
            cert_name=str(raw.get("cert_name") or ""),
            route=RoutePolicy.from_dict(raw["route"]) if raw.get("route") else None,
        )

    def _read_site_config(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _git_remote(path: Path) -> str:
        from pilot.internal.git import GitRepo

        return GitRepo(path).remote_url

    @staticmethod
    def _git_branch(path: Path) -> str:
        from pilot.internal.git import GitRepo

        return GitRepo(path).branch

    def get_configured_app(self, name: str) -> "AppConfig":
        """The bench.toml entry for this app, or an empty one when it has none. Its branch
        covers a pinned checkout that leaves HEAD detached."""
        from pilot.config import AppConfig

        return next(
            (app for app in self.bench.config.apps if app.name == name),
            AppConfig(name=name, repo="", branch=""),
        )
