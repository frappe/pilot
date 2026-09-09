from __future__ import annotations

import ast
import sys
import typing
from collections.abc import Iterable, Iterator
from pathlib import Path

from pilot.core.app.validator.base import bench_table, python_files
from pilot.core.app.validator.utils.module_resolver import ModuleResolver
from pilot.core.app.validator.utils.tmp_env import (
    TmpEnv,
    missing_modules,
    unimportable_modules,
)
from pilot.exceptions import AppValidationError, BenchError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class ImportCheck:
    """Validates imports in a throwaway venv without executing app modules."""

    def __init__(self) -> None:
        self.tmp_env = TmpEnv()

    def run(self, app: "App") -> None:
        locations = self._imported_module_locations(app)
        unresolved = self._get_on_disk_resolver(app).unresolved(locations)
        unresolved = self._get_missing_from_bench_env(app, unresolved)
        if not unresolved:
            return  # everything imported is already on the bench - nothing to install

        # Whatever is left has to come from this app's own dependencies, so
        # install it in a throwaway venv and ask there.
        try:
            self.tmp_env.create(app.bench)
            self.tmp_env.install_app(app, self._dependency_paths(app))
            self._check_imports(app, locations, unresolved)
        finally:
            self.tmp_env.delete()

    @staticmethod
    def _get_on_disk_resolver(app: "App") -> ModuleResolver:
        """Resolve against what the bench already has: the app's own package, the
        other apps' source trees (installed editable, so site-packages has no
        directory for them), and the bench env's third-party packages.
        """
        env_site_packages = next(app.bench.env_path.glob("lib/python*/site-packages"), None)
        roots = [app.path, *(installed.path for installed in app.bench.apps())]
        return ModuleResolver(*roots, *([env_site_packages] if env_site_packages else []))

    @staticmethod
    def _get_missing_from_bench_env(app: "App", unresolved: list[str]) -> list[str]:
        """Ask the bench's python about third-party modules stat couldn't find.

        A package can register submodules when it is imported, so they have no file
        to stat - `apiclient.discovery` is an alias for a googleapiclient module.
        App modules are left out: their files are right there, so stat is the last
        word, and find_spec would import the app being validated.
        """
        app_modules = {app.module_name, *(installed.config.name for installed in app.bench.apps())}
        candidates = [name for name in unresolved if name.split(".", 1)[0] not in app_modules]
        python = app.bench.env_path / "bin" / "python"
        if not candidates or not python.exists():
            return unresolved

        missing = unimportable_modules(python, candidates)
        return [name for name in unresolved if name not in candidates or name in missing]

    @staticmethod
    def _dependency_paths(app: "App") -> list[Path]:
        """Paths of the apps this one declares, to install alongside it.

        Installing every bench app instead would be both slow and wrong: apps that
        pin conflicting versions of a shared package coexist fine when installed
        one at a time, as the real environment does, but cannot be resolved
        together - so an unrelated pair of apps would fail this app's validation.
        """
        # Read the table directly rather than through DependencyDeclarationsCheck,
        # which rejects an app that has no pyproject.toml. This check also runs on
        # update, where an app is allowed to predate that rule.
        declared = bench_table(app).get("frappe-dependencies", {})
        paths: list[Path] = []
        if not isinstance(declared, dict):
            return paths  # DependencyDeclarationsCheck reports the bad table on install
        for name in declared:
            if name in ("frappe", app.config.name):
                continue  # frappe is installed first; the app itself comes last
            try:
                paths.append(app.bench.app(name).path)
            except BenchError:
                continue  # not installed - surfaces as an unresolved import instead
        return paths

    def _check_imports(self, app: "App", locations: dict[str, list[str]], unresolved: list[str]) -> None:
        reasons = missing_modules(self.tmp_env.python, unresolved)
        if not reasons:
            return  # find_spec disagrees with the stat check - nothing's actually missing

        lines = [
            f"{module}: {reason}\n    imported at: {', '.join(locations[module])}"
            for module, reason in reasons.items()
        ]
        raise AppValidationError(
            f"'{app.config.name}' has imports that don't resolve:\n"
            + "\n".join(lines)
            + "\nAdd the missing packages to pyproject.toml's dependencies, or fix the import path."
        )

    def _imported_module_locations(self, app: "App") -> dict[str, list[str]]:
        stdlib = sys.stdlib_module_names
        locations: dict[str, list[str]] = {}
        for path in python_files(app):
            relpath = path.relative_to(app.path)
            if self._is_test_file(relpath):
                continue
            for module, lineno in self._file_imported_modules(app, path):
                if module.split(".", 1)[0] in stdlib:
                    continue
                where = f"{relpath}:{lineno}"
                locations.setdefault(module, [])
                if where not in locations[module]:
                    locations[module].append(where)
        return locations

    @staticmethod
    def _is_test_file(relpath: Path) -> bool:
        # Test-only imports (responses, time_machine, ...) come from dev extras
        # a plain pip install never provides, so they'd always fail to resolve.
        if "tests" in relpath.parts[:-1]:
            return True
        return relpath.name.startswith("test_") or relpath.name == "conftest.py"

    def _file_imported_modules(self, app: "App", path: Path) -> list[tuple[str, int]]:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except OSError:
            return []

        modules: list[tuple[str, int]] = []
        for node in self._runtime_imports(tree.body):
            if isinstance(node, ast.Import):
                modules.extend((alias.name, node.lineno) for alias in node.names)
            else:
                modules.append((self._resolve_module(app, path, node), node.lineno))
        return modules

    def _runtime_imports(self, nodes: Iterable[ast.AST]) -> Iterator[ast.Import | ast.ImportFrom]:
        """Yield imports that must resolve at module import time. Imports inside
        functions are lazy and often intentionally dynamic, so they're skipped."""
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
            elif isinstance(node, (ast.Try, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            elif isinstance(node, ast.If) and self._is_type_checking(node.test):
                yield from self._runtime_imports(node.orelse)
            else:
                yield from self._runtime_imports(ast.iter_child_nodes(node))

    @staticmethod
    def _is_type_checking(test: ast.expr) -> bool:
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    @staticmethod
    def _resolve_module(app: "App", path: Path, node: ast.ImportFrom) -> str:
        if node.level == 0:
            # `from module import ...` - always has a module name (never bare).
            return typing.cast("str", node.module)

        parts = path.relative_to(app.path).with_suffix("").parts[:-1]
        cut = node.level - 1
        if cut >= len(parts):
            raise AppValidationError(
                f"'{app.config.name}' has an invalid relative import in "
                f"{path.relative_to(app.path)} (line {node.lineno}): "
                "goes above the app's own package."
            )
        base = ".".join(parts[: len(parts) - cut])
        return f"{base}.{node.module}" if node.module else base
