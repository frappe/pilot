"""
Parse and read the marketplace based on the current version of frappe running
Show install for apps that are compatibile with the version mentioned in the apps_v2.json only
When install is clicked instead of showing a dropdown of branches just install the expected frappe version compliant branch.
"""

import json
import typing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pilot.exceptions import BenchError
from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

_REGISTRY_V2_PATH = Path(__file__).parent.parent.parent / "registry" / "apps_v2.json"


@dataclass
class Resolver:
    app: str
    repo: str
    target_type: Literal["tag", "branch", "target"]
    target: str
    version: str
    frappe_version: str
    required_version: str
    is_installable: bool
    dependencies: dict[str, str] = field(default_factory=dict)
    title: str = ""
    description: str = ""
    logo_url: str = ""
    category: str = ""
    categories: list[str] = field(default_factory=list)
    stars: int | None = 0
    documentation: str = ""
    website: str = ""
    _registry: dict[str, list["Resolver"]] = field(default_factory=dict, init=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "name": self.app,
            "repo": self.repo,
            "target_type": self.target_type,
            "target": self.target,
            "version": self.version,
            "frappe_version": self.frappe_version,
            "required_version": self.required_version,
            "dependencies": self.dependencies,
            "is_installable": self.is_installable,
            "title": self.title,
            "description": self.description,
            "logo_url": self.logo_url,
            "category": self.category,
            "categories": self.categories,
            "stars": self.stars,
            "documentation": self.documentation,
            "website": self.website,
        }

    def _resolve(
        self,
        app: str,
        required_spec: str,
        visited: dict[str, str],
        path: list[str],
        result: list["Resolver"],
    ):
        if app in path:
            cycle = " -> ".join(path[path.index(app) :] + [app])
            raise BenchError(f"Circular dependency detected: {cycle}")
        if app in visited:
            if required_spec and Version(visited[app]) not in SpecifierSet(required_spec):
                raise BenchError(
                    f"Version conflict: '{app}' {visited[app]!r} already selected "
                    f"but {required_spec!r} is required by '{path[-1]}'."
                )
            return

        path.append(app)
        candidate_resolvers = self._registry.get(app, [])
        spec = SpecifierSet(required_spec) if required_spec else None
        resolver = next(
            (r for r in candidate_resolvers if spec is None or Version(r.version) in spec),
            None,
        )
        if not resolver:
            raise BenchError(
                f"Dependency '{app}' has no version satisfying {required_spec!r} "
                f"compatible with Frappe {self.frappe_version}.\n"
                f"Needed by '{path[-2]}' in the marketplace registry."
            )

        for dep, dep_spec in resolver.dependencies.items():
            self._resolve(dep, dep_spec, visited, path, result)
        result.append(resolver)

        visited[app] = resolver.version
        path.pop()

    def resolve(self) -> list["Resolver"]:
        """Returns dependencies in install order (deepest first, self last)."""
        if not self.is_installable:
            raise BenchError(
                f"'{self.app}' is not compatible with the current Frappe version.\nRequired: {self.required_version} Current: {self.frappe_version}"
            )
        result: list["Resolver"] = []
        visited: dict[str, str] = {}
        for dep, spec in self.dependencies.items():
            self._resolve(dep, spec, visited, [self.app], result)
        result.append(self)
        return result


@dataclass
class Marketplace:
    bench: "Bench"
    frappe_version: str = field(default="", init=False)

    def __post_init__(self):
        self.frappe_version = self.get_current_frappe_version()
        # Snapshot at construction so callers reading _REGISTRY_V2_PATH (incl. tests) see it.
        self._registry = self._parse_registry(json.loads(_REGISTRY_V2_PATH.read_text()))

    def get_current_frappe_version(self) -> str:
        """We need the current framework version to correctly suggest apps for installation"""
        cmd = [str(self.bench.env_path / "bin" / "python"), "-c", "import frappe; print(frappe.__version__)"]
        result = run_command(cmd)
        return result.stdout.strip().decode()

    @staticmethod
    @lru_cache(maxsize=1)
    def registry() -> list[dict]:
        """Parsed registry for callers that don't have a Marketplace/bench (e.g. tasks). Cached once."""
        return Marketplace._parse_registry(json.loads(_REGISTRY_V2_PATH.read_text()))

    @staticmethod
    def _parse_registry(raw: list[dict]) -> list[dict]:
        for app in raw:
            for target in app.get("targets", []):
                # Loads in the >=17.0.0-dev,<18.0.0 version specifier for each target
                target["_spec"] = SpecifierSet(target["frappe_core"], prereleases=True)
        return raw

    def _make_resolver(self, app: dict, target: dict, is_installable: bool) -> "Resolver":
        return Resolver(
            app=app["name"],
            repo=app["repo"],
            target_type=target.get("target_type", ""),
            target=target.get("target", ""),
            version=target.get("version", ""),
            frappe_version=self.frappe_version,
            required_version=target.get("frappe_core", ""),
            dependencies=target.get("dependencies", {}),
            title=app.get("title", app["name"]),
            description=app.get("description", ""),
            logo_url=app.get("logo_url", ""),
            category=app.get("category", ""),
            categories=app.get("categories", []),
            stars=app.get("stars") or 0,
            documentation=app.get("documentation", ""),
            website=app.get("website", ""),
            is_installable=is_installable,
        )

    def read_all_apps(self) -> list[Resolver]:
        resolvers = []
        dependency_lookup: dict[str, list[Resolver]] = {}
        current_frappe = Version(self.frappe_version)

        for app in self._registry:
            targets = app.get("targets", [])
            compatible_targets = [t for t in targets if current_frappe in t["_spec"]]
            best_match = compatible_targets[0] if compatible_targets else None
            display_target = best_match or (targets[0] if targets else {})

            resolvers.append(self._make_resolver(app, display_target, is_installable=bool(best_match)))

            if compatible_targets:
                dependency_lookup[app["name"]] = [
                    self._make_resolver(app, t, is_installable=True) for t in compatible_targets
                ]

        for resolver in resolvers:
            resolver._registry = dependency_lookup
        return resolvers
