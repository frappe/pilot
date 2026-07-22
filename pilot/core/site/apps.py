from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.site import Site


class SiteApps:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def install_app(self, app: "App") -> None:
        run_command(
            self.site._frappe_call("frappe", "--site", self.site.config.name, "install-app", app.config.name),
            cwd=self.site.bench.sites_path,
            stream_output=True,
        )
        self.site.bench.reload_workers(raises=True)

    def install_app_with_dependencies(self, app: "App") -> list["App"]:
        self.site.install_app(app)
        from pilot.core.app.validator.dependency_declarations import DependencyDeclarationsCheck

        required = DependencyDeclarationsCheck().get_hooks_required_apps(app)
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
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)
        self.site.bench.reload_workers(raises=True)

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

        config_path = self.site.path / "site_config.json"
        if not config_path.exists():
            raise BenchError(f"Site '{self.site.config.name}' does not exist.")
        try:
            site_config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            site_config = {}
        return list_installed_apps(site_config, self.site.bench.path, self.site.config.name)

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
