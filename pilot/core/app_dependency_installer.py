"""Resolves and installs an app's marketplace dependencies onto a bench."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pilot.core.marketplace import Marketplace, Resolver
from pilot.exceptions import AppNotFoundError, BenchError, DependencyResolutionError

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench


class AppDependencyInstaller:
    """Installs an app's missing marketplace dependencies and returns every
    dependency App (fresh or pre-existing) for callers to install on sites."""

    def __init__(self, bench: "Bench", app: "App") -> None:
        self.bench = bench
        self.app = app

    def install(self) -> list["App"]:
        resolver = self._find_resolver()
        if resolver is None:
            return []
        self._install_missing(resolver)
        return self._dependency_apps(resolver)

    def _find_resolver(self) -> Resolver | None:
        try:
            return Marketplace(self.bench).find_app(self.app.config.name)
        except AppNotFoundError:
            missing = self._missing_required_apps()
            if missing:
                raise BenchError(
                    f"'{self.app.config.name}' isn't in the marketplace registry, so its "
                    f"dependencies can't be installed automatically. It requires {missing} "
                    "— run 'bench get-app <repo>' for each of them first, then retry."
                )
            return None

    def _install_missing(self, resolver: Resolver) -> None:
        if all(self.bench.is_app_installed(dep) for dep in resolver.dependencies):
            return

        from pilot.commands.get_app import GetAppCommand

        try:
            dependency_chain = resolver.resolve()
        except DependencyResolutionError as exc:
            raise DependencyResolutionError(
                f"Could not resolve dependencies for '{self.app.config.name}':\n{exc}\n"
                "Manually install the dependencies before retrying."
            ) from exc

        for dep in dependency_chain[:-1]:  # exclude self (last entry)
            if dep.app == "frappe" or self.bench.is_app_installed(dep.app):
                continue
            print(f"Installing dependency '{dep.app}'...")
            sys.stdout.flush()
            # transitive deps already handled by earlier entries in the chain
            GetAppCommand(
                self.bench, dep.repo, dep.target, install_dependencies=False, skip_validations=True
            ).run()

    def _dependency_apps(self, resolver: Resolver) -> list["App"]:
        try:
            names = [dep.app for dep in resolver.resolve()[:-1]]  # exclude self (last entry)
        except DependencyResolutionError:
            # A deeper transitive conflict shouldn't hide direct deps we
            # already know are installed — fall back to those.
            names = list(resolver.dependencies)

        apps = []
        for name in names:
            if name == "frappe":
                continue
            try:
                apps.append(self.bench.app(name))
            except BenchError:
                continue
        return apps

    def _missing_required_apps(self) -> list[str]:
        from pilot.core.app_validator.dependency_declarations import DependencyDeclarationsCheck

        required = DependencyDeclarationsCheck()._get_pyproject_required_apps(self.app)
        return [name for name in required if not self.bench.is_app_installed(name)]
