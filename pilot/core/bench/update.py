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
        pins = pins or {}

        updated = []
        for app in self.bench.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            if app.is_linked:
                on_progress(f"{app.config.name} is a linked checkout, skipping.")
                continue
            pin = pins.get(app.config.name) or (marketplace_pin(app) if live_lookup else None)
            if pin is None and not self._follows_its_branch(app, live_lookup):
                on_progress(f"{app.config.name} is at the newest advertised release, skipping.")
                continue
            on_progress(f"Updating {app.config.name}...")
            try:
                app.update(pin=pin)
            except CommandError as error:
                print(f"  Error updating {app.config.name}: {error}", file=sys.stderr)
                raise MigrateError(f"Failed to update {app.config.name}") from error
            updated.append(app)

        # Every app is on its target revision now, so the checks see the tree
        # migrate will actually run against. Failing here means nothing has been
        # installed or built yet, and reverting is a checkout.
        for app in updated:
            on_progress(f"Validating {app.config.name}...")
            app.validate()

    @staticmethod
    def _follows_its_branch(app: "App", live_lookup: bool) -> bool:
        return live_lookup and not app.is_marketplace

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
                raise MigrateError(f"Failed to install app {app.config.name}: {error}") from error

    def rebuild_assets(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.environment import PythonEnvManager

        python_env = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Updating assets for {app.config.name}...")
            python_env.build_assets_for_app(app)


def marketplace_pin(app: "App") -> "RevisionPin | None":
    """The newer advertised release for a marketplace app, or None when there is none."""
    return app.update_target() if app.is_marketplace else None
