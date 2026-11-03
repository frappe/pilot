from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.app import App

CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
# Credential stores no app may bring back into the sandbox, nor any directory above them.
DENIED_HOME_PATHS = (".ssh", ".gnupg", ".config/systemd", ".aws", ".kube")


@dataclass(frozen=True)
class SandboxPaths:
    """Home paths an app asks the sandbox to keep: `read_paths` read-only, `write_paths` writable."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()

    @classmethod
    def from_declaration(
        cls, declaration: dict, label: str, error: Callable[[str], Exception]
    ) -> SandboxPaths:
        return cls(
            read=cls._get_paths(declaration, "read_paths", label, error),
            write=cls._get_paths(declaration, "write_paths", label, error),
        )

    @classmethod
    def _get_paths(
        cls, declaration: dict, field: str, label: str, error: Callable[[str], Exception]
    ) -> tuple[str, ...]:
        value = declaration.get(field, [])
        if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
            raise error(f"{label} {field} must be a list of strings.")
        return tuple(cls._get_path(path, f"{label} {field}", error) for path in value)

    @staticmethod
    def _get_path(path: str, label: str, error: Callable[[str], Exception]) -> str:
        if CONTROL_CHARACTER_PATTERN.search(path):
            raise error(f"{label} '{path}' must not contain control characters.")
        expanded = PurePath(Path.home() / path[2:] if path.startswith("~/") else path)
        if not expanded.is_absolute() or ".." in expanded.parts:
            raise error(f"{label} '{path}' must be absolute or start with ~/, without '..'.")
        for denied in (Path.home() / name for name in DENIED_HOME_PATHS):
            if denied.is_relative_to(expanded) or expanded.is_relative_to(denied):
                raise error(f"{label} '{path}' reaches {denied}, which the sandbox never exposes.")
        return str(expanded)


class AppSandbox:
    """The `[tool.pilot.sandbox]` table of an app's pyproject.toml."""

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
        return SandboxPaths.from_declaration(declaration, "[tool.pilot.sandbox]", error)
