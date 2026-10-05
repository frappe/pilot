from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.integrations.marketplace import Marketplace
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.site import Site


class SiteApps:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def install_app(self, app: "App") -> None:
        if app.config.name in self.disabled_apps():
            self.enable_app(app)
            return
        # Frappe fails deep in a traceback when a required app is not on the bench.
        missing = [name for name in self.get_required_apps(app) if not self.site.bench.is_app_installed(name)]
        if missing:
            raise BenchError(
                f"'{app.config.name}' requires {', '.join(missing)}, which this bench does not have. "
                "Add it with 'pilot get-app <repo>' first, then retry."
            )
        self._clear_cache()
        try:
            run_command(
                self.site._frappe_call(
                    "frappe", "--site", self.site.config.name, "install-app", app.config.name
                ),
                cwd=self.site.bench.sites_path,
                stream_output=True,
            )
        finally:
            self._clear_cache()

    def install_app_with_dependencies(self, app: "App") -> list["App"]:
        self.site.install_app(app)
        required = self.get_required_apps(app)
        dependencies = []
        for name in required:
            try:
                dependencies.append(self.site.bench.app(name))
            except BenchError:
                continue
        return dependencies

    def uninstall_app(self, app: "App", force: bool) -> None:
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "uninstall-app",
            app.config.name,
            "--yes",
            "--no-backup",
        )
        if force:
            cmd.append("--force")
        try:
            run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)
        finally:
            self._clear_cache()

    def disabled_apps(self) -> list[str]:
        from pilot.core.site.config import query_disabled_apps_via_db

        return query_disabled_apps_via_db(self.site.bench.path, self.site.config.name)

    def enable_app(self, app: "App") -> None:
        """Bring a disabled app back on the site. Its schema and data never left, so this
        is a flag flip - but Frappe refuses while an app it requires is still off, so
        those come back first: disabled ones flipped, missing ones installed."""
        self._enable_with_dependencies(app, set())

    def _enable_with_dependencies(self, app: "App", seen: set[str]) -> None:
        """`seen` carries the apps already handled, so a cycle in required_apps stops."""
        seen.add(app.config.name)
        required = [name for name in self.get_required_apps(app) if name not in seen]
        if required:
            disabled, installed = set(self.disabled_apps()), set(self.installed_apps())
            for name in required:
                dependency = self.site.bench.app(name)
                if name in disabled:
                    self._enable_with_dependencies(dependency, seen)
                elif name not in installed:
                    self.install_app(dependency)
        self._toggle_app("enable-app", app)

    def get_required_apps(self, app: "App") -> list[str]:
        """Frappe validates these itself and names what is missing, so an unreadable
        hooks.py only costs us the chance to bring a dependency back automatically."""
        from pilot.core.app.validator.dependency_declarations import DependencyDeclarationsCheck

        try:
            return DependencyDeclarationsCheck().get_hooks_required_apps(app)
        except OSError:
            return []

    def get_missing_dependencies(self, app: "App") -> list[str]:
        """Apps this one requires that the site does not have at all. Installing those
        is task work, so a caller wanting a quick enable should queue instead."""
        installed = set(self.installed_apps())
        return [name for name in self.get_required_apps(app) if name not in installed]

    def disable_app(self, app: "App") -> None:
        """Take the app out of use on the site, keeping its schema and data."""
        if not self.site.bench.has_app_disabling:
            raise BenchError("This bench's Frappe version cannot disable apps. Uninstall it instead.")
        if app.config.name not in Marketplace.registry_by_name():
            raise BenchError(f"App '{app.config.name}' is not a marketplace app and cannot be disabled.")
        self._toggle_app("disable-app", app)

    def _toggle_app(self, command: str, app: "App") -> None:
        """Captured, not streamed: a waiting caller needs Frappe's reason for refusing.
        Bounded under the admin gunicorn's 120s so a stuck toggle still answers."""
        run_command(
            self.site._frappe_call("frappe", "--site", self.site.config.name, command, app.config.name),
            cwd=self.site.bench.sites_path,
            timeout=110,
        )

    def _clear_cache(self) -> None:
        run_command(
            self.site._frappe_call("frappe", "--site", self.site.config.name, "clear-cache"),
            cwd=self.site.bench.sites_path,
        )

    def list_apps(self) -> list[str]:
        result = subprocess.run(
            self.site._frappe_call("frappe", "--site", self.site.config.name, "list-apps"),
            cwd=str(self.site.bench.sites_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def installed_apps(self) -> list[str]:
        from pilot.core.site.config import list_installed_apps

        return list_installed_apps(self._site_config(), self.site.bench.path, self.site.config.name)

    def active_apps(self) -> list[str]:
        from pilot.core.site.config import list_active_apps

        return list_active_apps(self._site_config(), self.site.bench.path, self.site.config.name)

    def _site_config(self) -> dict:
        config_path = self.site.path / "site_config.json"
        if not config_path.exists():
            raise BenchError(f"Site '{self.site.config.name}' does not exist.")
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def uninstall_apps(
        self,
        app_names: list[str],
        force: bool,
        on_progress: Callable[[str], None],
    ) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site.config.name}' does not exist.")

        installed = self.site.list_apps()
        for app_name in app_names:
            app = self.site.bench.app(app_name)
            if not force and installed and app.config.name not in installed:
                raise BenchError(f"App '{app_name}' is not installed on site '{self.site.config.name}'.")
            on_progress(f"Uninstalling '{app_name}' from site '{self.site.config.name}'...")
            self.site.uninstall_app(app, force=force)
            on_progress(f"'{app_name}' uninstalled from '{self.site.config.name}'.")
            self.remove_app_if_not_on_any_site(app_name, on_progress)

    def remove_app_if_not_on_any_site(
        self,
        app_name: str,
        on_progress: Callable[[str], None],
    ) -> None:
        for site in self.site.bench.sites():
            installed_apps = site.list_apps()
            if len(installed_apps) == 0 or app_name in installed_apps:
                return
        on_progress(f"\nApp {app_name} is not installed on any site removing from bench.")
        self.site.bench.app(app_name).remove(on_progress=on_progress)
