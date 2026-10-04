"""Resolve installable apps and their dependency versions against the bench's current Frappe version."""

import typing
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Literal

from pilot._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
from pilot._vendor.packaging.version import InvalidVersion, Version
from pilot.exceptions import AppNotFoundError, DependencyResolutionError
from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass
class Resolver:
    app: str
    repo: str
    branch: str
    commit: str
    channel: Literal["stable", "nightly"]
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
            "branch": self.branch,
            "commit": self.commit,
            "channel": self.channel,
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
            cycle = " -> ".join([*path[path.index(app) :], app])
            raise DependencyResolutionError(f"Circular dependency detected: {cycle}")
        if app in visited:
            if required_spec and Version(visited[app]) not in SpecifierSet(required_spec):
                raise DependencyResolutionError(
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
            raise DependencyResolutionError(
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
            raise DependencyResolutionError(
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
        # Snapshot at construction so callers see a consistent registry for this instance.
        self._registry = self._load_registry()

    @staticmethod
    def _load_registry() -> list[dict]:
        from pilot.core.registry_cache import RegistryCache
        from pilot.utils import cli_root

        return RegistryCache(cli_root()).load()

    def get_current_frappe_version(self) -> str:
        cmd = [
            str(self.bench.env_path / "bin" / "python"),
            "-c",
            "import frappe; print(frappe.__version__)",
        ]
        result = run_command(cmd)
        return result.stdout.strip().decode()

    @staticmethod
    def registry() -> list[dict]:
        """The app index for callers without a Marketplace/bench (e.g. tasks), reread when
        the registry clone changes."""
        from pilot.core.registry_cache import RegistryCache
        from pilot.utils import cli_root

        return Marketplace._registry_at(_modified_ns(RegistryCache(cli_root()).index_path))

    @staticmethod
    @lru_cache(maxsize=1)
    def _registry_at(modified_ns: int) -> list[dict]:
        return Marketplace._load_registry()

    @staticmethod
    def registry_by_name() -> dict[str, dict]:
        """The index keyed by app name, so callers look an app up without indexing it themselves."""
        return {entry["name"]: entry for entry in Marketplace.registry()}

    @staticmethod
    def releases(app_name: str) -> tuple[dict, ...]:
        """One app's releases, reread when its registry file changes."""
        from pilot.core.registry_cache import RegistryCache
        from pilot.utils import cli_root

        registry = RegistryCache(cli_root())
        return Marketplace._releases_at(app_name, _modified_ns(registry.path / "apps" / f"{app_name}.json"))

    @staticmethod
    @lru_cache(maxsize=512)
    def _releases_at(app_name: str, modified_ns: int) -> tuple[dict, ...]:
        from pilot.core.registry_cache import RegistryCache
        from pilot.utils import cli_root

        return Marketplace._newest_first(RegistryCache(cli_root()).releases(app_name))

    @staticmethod
    def _newest_first(releases: list[dict]) -> tuple[dict, ...]:
        return tuple(sorted(releases, key=Marketplace._version_key, reverse=True))

    @staticmethod
    def _version_key(release: dict) -> Version:
        try:
            return Version(release.get("version") or "")
        except InvalidVersion:
            return Version("0")

    @staticmethod
    def _is_compatible(release: dict, frappe_version: Version) -> bool:
        """An unparseable or absent frappe_core never matches - a release that
        doesn't say what it supports isn't offered as installable."""
        spec = Marketplace._safe_spec(release.get("frappe_core"))
        if not spec:
            return False
        return frappe_version in spec

    @staticmethod
    @cache
    def _safe_spec(frappe_core: str | None) -> SpecifierSet | None:
        """None means unparseable - excluded from compatibility matching."""
        try:
            return SpecifierSet(frappe_core or "", prereleases=True)
        except InvalidSpecifier:
            return None

    def _make_resolver(self, app: dict, release: dict, is_installable: bool) -> "Resolver":
        return Resolver(
            app=app["name"],
            repo=app["repo"],
            branch=release.get("branch", ""),
            commit=release.get("commit", ""),
            channel=release.get("channel", "stable"),
            version=release.get("version", ""),
            frappe_version=self.frappe_version,
            required_version=release.get("frappe_core") or "",
            dependencies=release.get("dependencies", {}),
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
            releases = self.releases(app["name"])
            compatible = self._preferred_channel(
                [r for r in releases if self._is_compatible(r, current_frappe)]
            )
            best_match = compatible[0] if compatible else None
            display_release = best_match or (releases[0] if releases else {})

            resolvers.append(self._make_resolver(app, display_release, is_installable=bool(best_match)))

            if compatible:
                dependency_lookup[app["name"]] = [
                    self._make_resolver(app, r, is_installable=True) for r in compatible
                ]

        for resolver in resolvers:
            resolver._registry = dependency_lookup
        return resolvers

    @staticmethod
    def _preferred_channel(compatible: list[dict]) -> list[dict]:
        """Stable releases, falling back to nightly ones when no stable release fits."""
        stable = [r for r in compatible if r.get("channel") != "nightly"]
        return stable or compatible

    def find_app(self, name: str) -> Resolver:
        """Look up a marketplace app by name, or raise AppNotFoundError - the
        single place every caller resolves a marketplace name to its Resolver."""
        resolver = next((r for r in self.read_all_apps() if r.app == name), None)
        if resolver is None:
            raise AppNotFoundError(f"'{name}' not found in marketplace.")
        return resolver


def _modified_ns(path: Path) -> int:
    """Cache key that changes when a registry refresh rewrites the file."""
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
