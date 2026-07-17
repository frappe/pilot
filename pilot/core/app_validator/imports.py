from __future__ import annotations

import ast
import sys
import typing
from collections.abc import Iterable, Iterator
from pathlib import Path

from pilot.core.app_validator.base import python_files
from pilot.core.app_validator.utils.module_resolver import ModuleResolver
from pilot.core.app_validator.utils.tmp_env import TmpEnv
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class ImportCheck:
    """Installs the app into a throwaway venv and verifies every import it
    makes actually resolves, without executing any module-level code."""

    def __init__(self) -> None:
        self.tmp_env = TmpEnv()

    def run(self, app: "App") -> None:
        try:
            self.tmp_env.create(app.bench.apps_path / "frappe")
            self.tmp_env.install_app(app, self._dependency_paths(app))
            self._check_imports(app)
        finally:
            self.tmp_env.delete()

    @staticmethod
    def _dependency_paths(app: "App") -> list[Path]:
        """Paths of this app's pyproject.toml-declared required apps that are
        already in the bench, so e.g. india_compliance's `import erpnext...`
        resolves against the real erpnext instead of failing as unresolved.
        This also assumes that the app is already installed in the bench by get-app,
        In case it isn't we will fail anyways saying that the import is unresolved which is true!!
        """
        from pilot.core.app_validator.dependency_declarations import DependencyDeclarationsCheck
        from pilot.exceptions import BenchError

        required = DependencyDeclarationsCheck()._get_pyproject_required_apps(app)
        paths = []
        for name in required:
            try:
                paths.append(app.bench.app(name).path)
            except BenchError:
                continue  # not installed — surfaces as an unresolved import instead
        return paths

    def _check_imports(self, app: "App") -> None:
        # Stat-based resolution first (fast, no code runs); anything it can't
        # find goes through find_spec in the tmp env for an authoritative error.
        locations = self._imported_module_locations(app)
        resolver = ModuleResolver(self.tmp_env.path)
        unresolved = resolver.unresolved(locations)
        if not unresolved:
            return

        reasons = self.tmp_env.resolve_modules(unresolved)
        if not reasons:
            return  # find_spec disagrees with the stat check — nothing's actually missing

        lines = [
            f"{module}: {reason}\n    imported at: {', '.join(locations[module])}"
            for module, reason in reasons.items()
        ]
        raise AppValidationError("Import errors:\n" + "\n".join(lines))

    def _imported_module_locations(self, app: "App") -> dict[str, list[str]]:
        stdlib = sys.stdlib_module_names
        locations: dict[str, list[str]] = {}
        for path in python_files(app):
            if self._is_test_file(path):
                continue
            relpath = path.relative_to(app.path)
            for module, lineno in self._file_imported_modules(app, path):
                if module.split(".", 1)[0] in stdlib:
                    continue
                where = f"{relpath}:{lineno}"
                locations.setdefault(module, [])
                if where not in locations[module]:
                    locations[module].append(where)
        return locations

    @staticmethod
    def _is_test_file(path: Path) -> bool:
        # Test-only imports (responses, time_machine, ...) come from dev extras
        # a plain pip install never provides, so they'd always fail to resolve.
        return path.name.startswith("test_") or path.name == "conftest.py"

    def _file_imported_modules(self, app: "App", path: Path) -> list[tuple[str, int]]:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except OSError:
            # We ideally should never hit SyntaxError here because we already validated syntax.
            return []

        modules: list[tuple[str, int]] = []
        for node in self._runtime_imports(tree.body):
            if isinstance(node, ast.Import):
                modules.extend((alias.name, node.lineno) for alias in node.names)
            else:
                modules.append((self._resolve_module(app, path, node), node.lineno))
        return modules

    def _runtime_imports(self, nodes: Iterable[ast.AST]) -> Iterator[ast.Import | ast.ImportFrom]:
        """Imports that must resolve at runtime — skips imports inside any
        try/except block (apps use these for optional dependencies) and
        `if TYPE_CHECKING:` blocks."""
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
            elif isinstance(node, ast.Try):
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
            # `from module import ...` — always has a module name (never bare).
            return typing.cast(str, node.module)

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
