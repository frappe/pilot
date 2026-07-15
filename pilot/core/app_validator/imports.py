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
            self.tmp_env.install_app(app)
            self._check_imports(app)
        finally:
            self.tmp_env.delete()

    def _check_imports(self, app: "App") -> None:
        # Stat-based resolution first (fast, no code runs); anything it can't
        # find goes through find_spec in the tmp env for an authoritative error.
        resolver = ModuleResolver(self.tmp_env.path)
        unresolved = resolver.unresolved(self._imported_modules(app))
        if unresolved:
            self.tmp_env.resolve_modules(unresolved)

    def _imported_modules(self, app: "App") -> list[str]:
        modules: set[str] = set()
        for path in python_files(app):
            if not self._is_test_file(path):
                modules.update(self._file_imported_modules(app, path))
        return sorted(m for m in modules if m.split(".")[0] not in sys.stdlib_module_names)

    @staticmethod
    def _is_test_file(path: Path) -> bool:
        # Test-only imports (responses, time_machine, ...) come from dev extras
        # a plain pip install never provides, so they'd always fail to resolve.
        return path.name.startswith("test_") or path.name == "conftest.py"

    def _file_imported_modules(self, app: "App", path: Path) -> set[str]:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except OSError:
            # We ideally should never hit SyntaxError here because we already validated syntax.
            return set()

        modules = set()
        for node in self._runtime_imports(tree.body):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            else:
                modules.add(self._resolve_module(app, path, node))
        return modules

    def _runtime_imports(
        self, nodes: Iterable[ast.AST]
    ) -> Iterator[ast.Import | ast.ImportFrom]:
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
