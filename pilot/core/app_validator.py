from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
import typing
from functools import cached_property
from pathlib import Path

from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class Validator:
    """Statically validates a cloned app before it's installed into the bench
    env, so a broken app is rejected up front instead of after `pip install`
    has already touched the environment."""

    def __init__(self, app: "App"):
        self.app = app
        self.module_path = app.path / app.module_name

    def validate(self) -> None:
        self.validate_repo_structure()
        self.validate_syntax()
        self.validate_internal_imports()
        self.validate_external_imports()

    def validate_repo_structure(self) -> None:
        if not (self.app.path / "pyproject.toml").exists():
            raise AppValidationError(f"'{self.app.config.name}' has no pyproject.toml.")
        if not self.module_path.is_dir():
            raise AppValidationError(f"'{self.app.config.name}' has no '{self.app.module_name}' package directory.")
        if not (self.module_path / "hooks.py").exists():
            raise AppValidationError(f"'{self.app.config.name}' is missing {self.app.module_name}/hooks.py.")

    def validate_syntax(self) -> None:
        broken = [
            f"{path.relative_to(self.app.path)}: {error}"
            for path in self._python_files()
            for error in self._syntax_errors(path)
        ]
        if broken:
            raise AppValidationError(
                f"'{self.app.config.name}' has Python syntax errors:\n" + "\n".join(f"  {b}" for b in broken)
            )

    @staticmethod
    def _syntax_errors(path: Path) -> list[str]:
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            return [f"line {exc.lineno}: {exc.msg}"]
        except OSError:
            return []
        return []

    def validate_internal_imports(self) -> None:
        broken = [
            f"{path.relative_to(self.app.path)}: {module}"
            for path, module in self._all_imports()
            if self._is_internal(module) and not self._resolves(module)
        ]
        if broken:
            raise AppValidationError(
                f"'{self.app.config.name}' has broken internal imports:\n" + "\n".join(f"  {b}" for b in broken)
            )

    def validate_external_imports(self) -> None:
        declared = self._declared_dependencies()
        missing = sorted(
            {
                self._top_level_package(module)
                for _, module in self._all_imports()
                if self._is_external(module) and self._distribution_name(module).casefold() not in declared
            }
        )
        if missing:
            raise AppValidationError(
                f"'{self.app.config.name}' imports packages not declared as dependencies: {', '.join(missing)}"
            )

    def _all_imports(self) -> list[tuple[Path, str]]:
        return [(path, module) for path in self._python_files() for module in self._imported_modules(path)]

    def _python_files(self) -> list[Path]:
        return list(self.module_path.rglob("*.py"))

    def _is_internal(self, module: str) -> bool:
        return module == self.app.module_name or module.startswith(self.app.module_name + ".")

    @staticmethod
    def _top_level_package(module: str) -> str:
        return module.split(".")[0]

    def _distribution_name(self, module: str) -> str:
        """Map an import's top-level name to its installed distribution name.

        `import bs4` and `pip install beautifulsoup4` refer to the same
        package under different names. Reads that mapping from packages
        already installed in the bench's own env (frappe and every app
        installed before this one) rather than a hand-maintained alias
        list — the app being validated isn't installed yet, so its own
        distribution metadata doesn't exist to consult.
        """
        root = self._top_level_package(module)
        return self._bench_import_to_distribution.get(root, root)

    @cached_property
    def _bench_import_to_distribution(self) -> dict[str, str]:
        bench_python = self.app.bench.python
        if not bench_python.exists():
            return {}
        result = subprocess.run(
            [str(bench_python), "-c", "import importlib.metadata, json; print(json.dumps(importlib.metadata.packages_distributions()))"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {}
        try:
            return {module: names[0].replace("-", "_") for module, names in json.loads(result.stdout).items()}
        except (json.JSONDecodeError, IndexError):
            return {}

    @staticmethod
    def _imported_modules(path: Path) -> list[str]:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, OSError):
            return []
        guarded = Validator._optional_import_ids(tree)
        modules = []
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.append(node.module)
        return modules

    @staticmethod
    def _optional_import_ids(tree: ast.AST) -> set[int]:
        """Node ids of imports inside the `try:` body of a `try/except ImportError` block.

        Apps commonly guard an optional dependency this way; such an import
        isn't a hard requirement, so it shouldn't be flagged as broken or
        undeclared. The except handler's body is left unguarded — a broken
        import shipped there is still a real bug.
        """
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not Validator._catches_import_error(node):
                continue
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))
        return guarded

    @staticmethod
    def _catches_import_error(node: ast.Try) -> bool:
        for handler in node.handlers:
            if handler.type is None:
                return True
            types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if any(isinstance(t, ast.Name) and t.id in ("ImportError", "ModuleNotFoundError") for t in types):
                return True
        return False

    def _resolves(self, module: str) -> bool:
        parts = module.split(".")
        candidate = self.app.path.joinpath(*parts)
        return candidate.with_suffix(".py").exists() or (candidate / "__init__.py").exists()

    def _is_external(self, module: str) -> bool:
        root = self._top_level_package(module)
        return root != self.app.module_name and root not in sys.stdlib_module_names

    def _declared_dependencies(self) -> set[str]:
        names = self._dependencies_declared_in(self.app.path)
        names.add("frappe")
        # Apps run inside the frappe framework's own environment, so any
        # dependency frappe itself declares (e.g. pypika) is already
        # installed and usable without every app redeclaring it.
        names |= self._dependencies_declared_in(self.app.bench.apps_path / "frappe")
        return names

    @staticmethod
    def _dependencies_declared_in(app_path: Path) -> set[str]:
        pyproject = app_path / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            return set()
        return {Validator._dependency_name(dep) for dep in data.get("project", {}).get("dependencies", [])}

    @staticmethod
    def _dependency_name(requirement: str) -> str:
        import re

        name = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0]
        return name.strip().replace("-", "_").casefold()
