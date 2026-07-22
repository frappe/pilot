from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.app import App


@dataclass(frozen=True)
class AppInstallResult:
    """Outcome of installing an app and any dependency apps installed with it."""

    app: "App"
    already_installed: bool
    installed_dependencies: list["App"]
