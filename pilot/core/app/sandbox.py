from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.app import App

CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
# An app may keep a directory below one of these roots, and nothing else.
ALLOWED_HOME_ROOTS = (".cache", ".local/share", ".local/state")
ALLOWED_SYSTEM_ROOTS = ("/opt", "/srv", "/var/lib", "/var/cache", "/usr/local/share")
# Bench-root folders an app may not claim: the admin's own, and config/, which has protected entries.
RESERVED_BENCH_FOLDERS = frozenset({"tmp", "migrations", "tasks", "config"})
# First directory below a root that holds credentials, runs at login, or is code the admin runs.
RESERVED_NAMES = frozenset(
    {"keyrings", "gnome-keyring", "kwalletd", "applications", "autostart", "systemd", "uv", "pilot", "pip"}
)


@dataclass(frozen=True)
class SandboxPaths:
    """Paths an app asks the sandbox to keep: `read_paths` read-only, `write_paths` writable.

    A path is `~/...` in the home, `/...` on the system, or relative to the bench the app is in."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()

    @classmethod
    def from_declaration(
        cls, declaration: dict, label: str, error: Callable[[str], Exception], bench_path: Path
    ) -> SandboxPaths:
        return cls(
            read=cls._get_paths(declaration, "read_paths", label, error, bench_path),
            write=cls._get_paths(declaration, "write_paths", label, error, bench_path),
        )

    @classmethod
    def _get_paths(
        cls, declaration: dict, field: str, label: str, error: Callable[[str], Exception], bench_path: Path
    ) -> tuple[str, ...]:
        value = declaration.get(field, [])
        if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
            raise error(f"{label} {field} must be a list of strings.")
        return tuple(cls._get_path(path, f"{label} {field}", error, bench_path) for path in value)

    @staticmethod
    def _get_path(path: str, label: str, error: Callable[[str], Exception], bench_path: Path) -> str:
        if CONTROL_CHARACTER_PATTERN.search(path):
            raise error(f"{label} '{path}' must not contain control characters.")
        if path.startswith("~") and not path.startswith("~/"):
            raise error(f"{label} '{path}' must start with ~/ to name a folder in the home directory.")
        if not path.startswith(("/", "~/")):
            return _get_bench_path(path, label, error, bench_path)
        expanded = Path.home() / path[2:] if path.startswith("~/") else Path(path)
        if not expanded.is_absolute() or ".." in expanded.parts:
            raise error(f"{label} '{path}' must be absolute or start with ~/, without '..'.")
        # The link target counts too, so a link inside an allowed root cannot lead out of it.
        resolved = Path(os.path.realpath(expanded))
        if not (is_allowed_path(expanded) and is_allowed_path(resolved)):
            raise error(
                f"{label} '{path}' must be a directory below one of: "
                f"{', '.join(f'~/{root}' for root in ALLOWED_HOME_ROOTS)}, {', '.join(ALLOWED_SYSTEM_ROOTS)}."
            )
        if is_protected_path(expanded, bench_path) or is_protected_path(resolved, bench_path):
            raise error(
                f"{label} '{path}' must not be in or above the benches directory or the Pilot install, "
                f"or in the home directory outside {', '.join(f'~/{root}' for root in ALLOWED_HOME_ROOTS)}."
            )
        return str(resolved)


def _get_bench_path(path: str, label: str, error: Callable[[str], Exception], bench_path: Path) -> str:
    """A folder in the bench the app is in. Its real path must stay in the bench and out of the folders
    the admin owns, so a link cannot lead out."""
    relative = PurePosixPath(path)
    if not relative.parts or ".." in relative.parts:
        raise error(f"{label} '{path}' must name a folder in the bench, without '..'.")
    bench_root = Path(os.path.realpath(bench_path))
    resolved = Path(os.path.realpath(bench_root / relative))
    inside = resolved.relative_to(bench_root).parts if resolved.is_relative_to(bench_root) else ()
    if not inside or inside[0].startswith(".") or inside[0] in RESERVED_BENCH_FOLDERS:
        raise error(
            f"{label} '{path}' must be a folder in the bench, not a hidden one or "
            f"{', '.join(sorted(RESERVED_BENCH_FOLDERS))}."
        )
    return str(resolved)


def is_allowed_path(path: Path) -> bool:
    """Whether `path` is a directory below an allowed root, not the root itself or a reserved name."""
    for root in _get_allowed_roots():
        if path.is_relative_to(root) and path != root:
            return path.relative_to(root).parts[0] not in RESERVED_NAMES
    return False


def is_protected_path(path: Path, bench_path: Path) -> bool:
    """Whether `path` is in or above a place the sandbox hides: the benches directory, the Pilot install,
    or the home directory outside the allowed home roots. An allowed system root can hold any of them."""
    hidden = [bench_path.parent, cli_root()]
    home_roots = _with_real_paths(Path.home() / root for root in ALLOWED_HOME_ROOTS)
    if not any(path.is_relative_to(root) for root in home_roots):
        hidden.append(Path.home())
    return any(path.is_relative_to(place) or place.is_relative_to(path) for place in _with_real_paths(hidden))


def _get_allowed_roots() -> set[Path]:
    home_roots = [Path.home() / root for root in ALLOWED_HOME_ROOTS]
    return _with_real_paths([*home_roots, *map(Path, ALLOWED_SYSTEM_ROOTS)])


def _with_real_paths(paths: Iterable[Path]) -> set[Path]:
    """The paths, and where each one leads when links are followed."""
    paths = set(paths)
    return paths | {Path(os.path.realpath(path)) for path in paths}


class AppSandbox:
    """The `[tool.pilot.sandbox]` table of an app's pyproject.toml. docs/sandbox.md explains the sandbox."""

    def __init__(self, app: App) -> None:
        self.app = app

    def get_paths(self, error: Callable[[str], Exception]) -> SandboxPaths:
        pyproject = self.app.path / "pyproject.toml"
        if not pyproject.exists():
            return SandboxPaths()
        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise error(f"invalid pyproject.toml: {exc}") from exc
        declaration = data.get("tool", {}).get("pilot", {}).get("sandbox", {})
        if not isinstance(declaration, dict):
            raise error("[tool.pilot.sandbox] must be a table.")
        return SandboxPaths.from_declaration(declaration, "[tool.pilot.sandbox]", error, self.app.bench.path)
