"""
Parse and read the marketplace based on the current version of frappe running
Show install for apps that are compatibile with the version mentioned in the apps_v2.json only
When install is clicked instead of showing a dropdown of branches just install the expected frappe version compliant branch.
"""

import json
import typing
from dataclasses import dataclass, field
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
    stars: int = 0
    _registry: dict[str, "Resolver"] = field(default_factory=dict, init=False, repr=False)

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
            "stars": self.stars,
        }

    def _resolve(
        self,
        app: str,
        visited: set[str],
        path: list[str],
        result: list["Resolver"],
    ):
        if app in path:
            cycle = " -> ".join(path[path.index(app) :] + [app])
            raise BenchError(f"Circular dependency detected: {cycle}")
        if app in visited:
            return

        path.append(app)
        resolver = self._registry.get(app)
        if not resolver:
            raise BenchError(
                f"Dependency '{app}' has no version compatible with Frappe {self.frappe_version}.\n"
                "It currently not support your current Frappe version."
            )

        for dep in resolver.dependencies:
            self._resolve(dep, visited, path, result)
        result.append(resolver)

        visited.add(app)
        path.pop()

    def resolve(self) -> list["Resolver"]:
        """Returns dependencies in install order (deepest first, self last)."""
        if not self.is_installable:
            raise BenchError(
                f"'{self.app}' is not compatible with the current Frappe version.\nRequired: {self.required_version} Current: {self.frappe_version}"
            )
        result: list["Resolver"] = []
        visited: set[str] = set()
        for dep in self.dependencies:
            self._resolve(dep, visited, [self.app], result)
        result.append(self)
        return result


@dataclass
class Marketplace:
    bench: "Bench"
    frappe_version: str = field(default="", init=False)

    def __post_init__(self):
        self.frappe_version = self.get_current_frappe_version()
        raw = json.loads(_REGISTRY_V2_PATH.read_text())
        self.registry = self._parse_registry(raw)

    def get_current_frappe_version(self) -> str:
        """We need the current framework version to correctly suggest apps for installation"""
        cmd = [str(self.bench.env_path / "bin" / "python"), "-c", "import frappe; print(frappe.__version__)"]
        result = run_command(cmd)
        return result.stdout.strip().decode()

    def _parse_registry(self, raw: list[dict]) -> list[dict]:
        for app in raw:
            for target in app.get("targets", []):
                # Loads in the >=17.0.0-dev,<18.0.0 version specifier for each target
                target["_spec"] = SpecifierSet(target["frappe_core"], prereleases=True)
        return raw

    def read_all_apps(self) -> list[Resolver]:
        results = []
        current = Version(self.frappe_version)
        for app in self.registry:
            # Checks if the current version is supported in the apps version specifier targets
            # preloaded while parsing the registry. If two branches support the same frappe version
            # We will only return the top most entry. Users will be notified about this during PRs
            targets = app.get("targets", [])
            match = next((t for t in targets if current in t["_spec"]), None)
            target = match or (targets[0] if targets else {})
            results.append(
                Resolver(
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
                    stars=app.get("stars", 0),
                    is_installable=bool(match),
                )
            )

        lookup = {r.app: r for r in results if r.is_installable}
        for r in results:
            # This is just sharing the pointer its' fine
            r._registry = lookup
        return results


if __name__ == "__main__":
    from pilot.core.bench import Bench, BenchConfig

    bench = Bench(
        BenchConfig.from_file(Path("/home/frappe/bench-cli/benches/test/bench.toml")),
        Path("/home/frappe/bench-cli/benches/test"),
    )
    marketplace = Marketplace(bench)
    # print(len(marketplace.registry))
    # print(len(marketplace.read_installable_apps()))
    for app in marketplace.read_all_apps():
        if app.app == "kenya_compliance_via_slade":
            import pprint

            pprint.pprint(app.resolve())
