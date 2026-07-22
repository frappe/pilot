from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.app import App, RevisionPin
    from pilot.core.bench import Bench


class BenchUpdater:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def update_apps(
        self,
        apps_filter: set | None,
        on_progress: Callable[[str], None],
        pins: dict[str, RevisionPin] | None = None,
    ) -> None:
        """Update each app to its pin, or resolve marketplace/branch targets live when `pins` is None (direct CLI update)."""
        import sys

        from pilot.exceptions import CommandError, MigrateError

        live_lookup = pins is None
        marketplace_by_name = self._marketplace_registry() if live_lookup else {}
        pins = pins or {}

        for app in self.bench.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Updating {app.config.name}...")
            pin = pins.get(app.config.name)
            if pin is None and live_lookup:
                pin = marketplace_pin(app, marketplace_by_name)
            try:
                app.update(pin=pin)
            except CommandError as error:
                print(f"  Error updating {app.config.name}: {error}", file=sys.stderr)
                raise MigrateError(f"Failed to update {app.config.name}") from error

    @staticmethod
    def _marketplace_registry() -> dict:
        from pilot.integrations.marketplace import Marketplace

        return {entry["name"]: entry for entry in Marketplace.registry()}

    def reinstall_apps(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.exceptions import CommandError, MigrateError
        from pilot.managers.environment import PythonEnvManager

        python_env = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Reinstalling {app.config.name}...")
            try:
                python_env.install_app(app)
            except CommandError as error:
                raise MigrateError(f"Failed to install app {app}: {error}") from error

    def rebuild_assets(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.environment import PythonEnvManager

        python_env = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Updating assets for {app.config.name}...")
            python_env.build_assets_for_app(app)

    def migrate_sites(self, skip_failing_patches: bool, on_progress: Callable[[str], None]) -> None:
        for site in self.bench.sites():
            on_progress(f"Migrating {site.config.name}...")
            site.migrate(skip_failing=skip_failing_patches)


def marketplace_pin(app: "App", marketplace_by_name: dict) -> "RevisionPin | None":
    entry = marketplace_by_name.get(app.config.name)
    if not entry or app.config.repo != entry.get("repo"):
        return None
    version = app.installed_version
    target = next((target for target in entry.get("targets", []) if target["version"] == version), None)
    if target is None:
        return None

    from pilot.core.app import RevisionPin

    return RevisionPin.from_marketplace_target(target)
