from __future__ import annotations

import json
import typing
from pathlib import Path

from pilot.core.app.validator.base import IgnoredPaths, module_path
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


# Their controllers write module files on insert, which fixture import has no module path for.
UNIMPORTABLE_DOCTYPES = ("DocType", "Page")


class FixturesCheck:
    """Parse every fixture file, since frappe imports them during migrate."""

    @staticmethod
    def _fixture_files(app: "App") -> list[Path]:
        ignored = IgnoredPaths(app)
        paths = sorted((module_path(app) / "fixtures").glob("*.json"))
        return [path for path in paths if not ignored.is_ignored(path)]

    def run(self, app: "App") -> None:
        broken = []
        unimportable = []
        for path in self._fixture_files(app):
            name = path.relative_to(app.path)
            try:
                records = json.loads(path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                broken.append(f"{name}: {exc}")
                continue

            if not isinstance(records, list):
                continue
            for doctype in UNIMPORTABLE_DOCTYPES:
                if any(isinstance(r, dict) and r.get("doctype") == doctype for r in records):
                    unimportable.append(f"{name}: contains '{doctype}' records")

        if broken:
            raise AppValidationError(
                f"'{app.config.name}' has fixtures that aren't valid JSON:\n"
                + "\n".join(f"  {problem}" for problem in broken)
                + "\nFix the JSON or drop the file - frappe imports these during migrate."
            )

        if unimportable:
            names = " and ".join(UNIMPORTABLE_DOCTYPES)
            raise AppValidationError(
                f"'{app.config.name}' has fixtures frappe cannot import:\n"
                + "\n".join(f"  {problem}" for problem in unimportable)
                + f"\n{names} records must ship in the app's modules, not as fixtures."
            )
