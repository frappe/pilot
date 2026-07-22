from __future__ import annotations

import typing
from pathlib import Path

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class ValidationCheck(typing.Protocol):
    """A single check run against a cloned app before it's installed."""

    def run(self, app: "App") -> None: ...


def module_path(app: "App") -> Path:
    return app.path / app.module_name


def python_files(app: "App") -> list[Path]:
    return list(module_path(app).rglob("*.py"))
