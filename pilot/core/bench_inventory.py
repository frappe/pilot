from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class BenchInventory:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def app(self, name: str) -> "App":
        from pilot.config.app import AppConfig
        from pilot.core.app import App

        app_path = self.bench.apps_path / name
        if not app_path.is_dir():
            app_path = self.bench.apps_path / name.replace("_", "-")

        if not app_path.is_dir():
            raise BenchError(f"App {name} not found in bench")

        return App(
            AppConfig(
                name=app_path.name,
                repo=self._git_remote(app_path),
                branch=self._git_branch(app_path),
            ),
            self.bench,
        )

    def apps(self) -> list["App"]:
        from pilot.config.app import AppConfig
        from pilot.core.app import App

        if not self.bench.apps_path.is_dir():
            return []
        result = []
        for app_path in sorted(self.bench.apps_path.iterdir()):
            if app_path.is_dir() and (app_path / ".git").exists():
                result.append(
                    App(
                        AppConfig(
                            name=app_path.name,
                            repo=self._git_remote(app_path),
                            branch=self._git_branch(app_path),
                        ),
                        self.bench,
                    )
                )
        return result

    def registered_apps(self) -> list[str]:
        apps_txt = self.bench.sites_path / "apps.txt"
        return apps_txt.read_text().splitlines() if apps_txt.exists() else []

    def is_app_installed(self, name: str) -> bool:
        from pilot.config.app import AppConfig
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
            if site_dir.is_dir() and config_path.exists():
                raw = self._read_site_config(config_path)
                result.append(Site(self._site_config(site_dir.name, raw), self.bench))
        return result

    def write_apps_txt(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        names = [app.config.name for app in self.apps()]
        apps_txt.write_text("\n".join(names) + "\n" if names else "")

    def _site_config(self, name: str, raw: dict):
        from pilot.config.site import SiteConfig

        raw_domains = [
            entry.get("domain") if isinstance(entry, dict) else entry
            for entry in (raw.get("domains") or [])
        ]
        domains = [domain for domain in raw_domains if isinstance(domain, str) and domain]
        host_name = (raw.get("host_name") or "").strip()
        primary = host_name.split("://", 1)[-1] if host_name else ""
        return SiteConfig(
            name=name,
            apps=[],
            ssl=bool(raw.get("ssl")),
            domains=domains,
            primary_domain=primary,
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
