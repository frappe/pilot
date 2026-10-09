from __future__ import annotations

import contextlib
import typing

from pilot._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
from pilot._vendor.packaging.version import InvalidVersion, Version
from pilot.core.app.validator.base import get_bench_python, module_path, read_pyproject
from pilot.core.app.validator.utils.bench_runner import run_in_bench
from pilot.exceptions import AppValidationError, BenchError

if typing.TYPE_CHECKING:
    from pilot.core.app import App

_VERSION_AST_SCRIPT = """
import ast, json, sys

req = json.load(sys.stdin)
path = req["path"]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        tree = ast.parse(f.read(), filename=path)
except SyntaxError as exc:
    print(json.dumps({"syntax_error": f"line {exc.lineno}: {exc.msg}"}))
    sys.exit(0)
except (OSError, UnicodeDecodeError):
    print(json.dumps({"version": None}))
    sys.exit(0)

version = None
for node in tree.body:
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
        continue
    if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
        version = str(node.value.value)
        break

print(json.dumps({"version": version}))
"""


class FrappeCompatibilityCheck:
    """Check the bench actually satisfies the app versions an app declares.

    DependencyDeclarationsCheck only validates the shape of
    `[tool.bench.frappe-dependencies]`. This compares it to what is installed,
    which is what catches a new revision that needs a newer frappe than the
    bench runs. An app that declares nothing is left alone, so an app older
    than the rule still updates.
    """

    def run(self, app: "App") -> None:
        declared = (read_pyproject(app) or {}).get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
        if not isinstance(declared, dict):
            return  # DependencyDeclarationsCheck reports a malformed table on install

        problems = [
            problem
            for name, specifier in declared.items()
            if (problem := self._mismatch(app, name, str(specifier)))
        ]
        if problems:
            raise AppValidationError(
                f"'{app.config.name}' needs app versions this bench doesn't have:\n"
                + "\n".join(f"  {problem}" for problem in problems)
                + f"\nUpdate the app it needs, or move '{app.config.name}' to a revision that "
                "supports what is installed."
            )

    def _mismatch(self, app: "App", name: str, specifier: str) -> str | None:
        """Why the installed `name` falls outside `specifier`, or None if it fits.

        None also means there is nothing to compare: the app isn't installed - the
        dependency installer reports that - or it states no readable version.
        """
        try:
            # Without prereleases the bench's own dev build matches nothing.
            allowed = SpecifierSet(specifier, prereleases=True)
        except InvalidSpecifier as exc:
            # Nothing else reads this table on update: VersionSpecifiersCheck
            # covers [project], and DependencyDeclarationsCheck is install-only.
            raise AppValidationError(
                f"'{app.config.name}' declares an unreadable version for '{name}' in "
                f"pyproject.toml's [tool.bench.frappe-dependencies]: {specifier!r} ({exc}).\n"
                'Use comma-separated PEP 440 ranges, e.g. frappe = ">=16.0.0,<17.0.0"'
            ) from exc

        version = self._installed_version(app, name)
        if version is None or version in allowed:
            return None
        return f"needs {name} {specifier}, but {version} is installed"

    @staticmethod
    def _installed_version(app: "App", name: str) -> Version | None:
        """The `__version__` the named app assigns at the top of its package."""
        try:
            target_app = app.bench.app(name)
            init_file = module_path(target_app) / "__init__.py"
            if not init_file.is_file():
                return None
        except (BenchError, OSError):
            return None

        bench_python = get_bench_python(app)
        data = run_in_bench(bench_python, _VERSION_AST_SCRIPT, {"path": str(init_file)})
        if "syntax_error" in data:
            raise AppValidationError(
                f"'{name}' has syntax errors in __init__.py: {data['syntax_error']}"
            )
        version_str = data.get("version")
        if version_str is not None:
            with contextlib.suppress(InvalidVersion):
                return Version(version_str)
        return None
