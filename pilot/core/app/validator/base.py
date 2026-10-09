from __future__ import annotations

import fnmatch
import tomllib
import typing
from pathlib import Path

from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class ValidationCheck(typing.Protocol):
    """A single check run against a cloned app before it's installed."""

    def run(self, app: "App") -> None: ...


def module_path(app: "App") -> Path:
    return app.path / app.module_name


def python_files(app: "App") -> list[Path]:
    ignored = IgnoredPaths(app)
    return [path for path in module_path(app).rglob("*.py") if not ignored.is_ignored(path)]


class IgnoredPaths:
    """The app's `[tool.bench] validation-ignore` globs, matched against paths.

    Patterns are relative to the app root and `*` crosses directory separators,
    so 'atlas/internal/*' excludes that whole subtree.
    """

    def __init__(self, app: "App") -> None:
        self.root = app.path
        self.patterns = self._patterns(app)

    def is_ignored(self, path: Path) -> bool:
        return self.matches(path.relative_to(self.root).as_posix())

    def matches(self, relpath: str) -> bool:
        """For callers that already hold a path relative to the app root."""
        return any(fnmatch.fnmatch(relpath, pattern) for pattern in self.patterns)

    @staticmethod
    def _patterns(app: "App") -> list[str]:
        patterns = bench_table(app).get("validation-ignore", [])
        if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
            raise AppValidationError(
                f"'{app.config.name}' has an invalid [tool.bench] validation-ignore in pyproject.toml.\n"
                'It must be a list of glob patterns, such as validation-ignore = ["atlas/internal/*"].'
            )
        return patterns


def bench_table(app: "App") -> dict:
    """The app's `[tool.bench]` table, or {} when it has none.

    Only `bench` is pilot's to police; the rest of `[tool]` belongs to other tools.
    """
    tool = (read_pyproject(app) or {}).get("tool")
    table = tool.get("bench", {}) if isinstance(tool, dict) else {}
    if not isinstance(table, dict):
        raise AppValidationError(
            f"'{app.config.name}' has an invalid [tool.bench] in pyproject.toml: expected a table, "
            f"got {type(table).__name__}."
        )
    return table


def read_pyproject(app: "App") -> dict | None:
    """The app's parsed pyproject.toml, or None when it has none.

    Checks run standalone during an update, so no one can assume
    RepoStructureCheck has already vetted the file.
    """
    path = app.path / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise AppValidationError(
            f"'{app.config.name}' has an invalid pyproject.toml: {exc}\nFix the TOML syntax."
        ) from exc


def _find_env_python(env_path: Path | str | None) -> str | None:
    if not env_path:
        return None
    env_dir = Path(env_path)
    if not env_dir.is_absolute():
        env_dir = env_dir.absolute()
    for rel_path in ("bin/python", "bin/python3", "Scripts/python.exe"):
        p = env_dir / rel_path
        if p.is_file():
            return str(p)
    return None


def get_bench_python(app: "App | None" = None) -> str:
    """Returns the path to the bench's Python binary, falling back to sys.executable."""
    import sys

    bench = getattr(app, "bench", None) if app is not None else None
    if bench is not None:
        env_bin = _find_env_python(getattr(bench, "env_path", None))
        if env_bin:
            return env_bin

        candidate = getattr(bench, "python", None)
        if candidate and not callable(candidate):
            candidate_path = Path(candidate)
            if not candidate_path.is_absolute():
                candidate_path = candidate_path.absolute()
            if candidate_path.is_file():
                return str(candidate_path)

    return sys.executable
