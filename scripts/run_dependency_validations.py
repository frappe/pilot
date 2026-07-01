#!/usr/bin/env python3
"""
Validate a marketplace app's declared dependencies.

Checks that every dependency an app declares already exists in the
marketplace with a version that satisfies the requested spec — otherwise
merging the app would break installs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).parent))
from validator import Validator


class DependencyValidator(Validator):
    name = "dependencies"

    def __init__(self, dependencies: dict[str, str], marketplace: dict[str, dict]) -> None:
        super().__init__()
        self.dependencies = dependencies
        self.marketplace = marketplace

    def validate(self) -> None:
        for name, spec in self.dependencies.items():
            app = self.marketplace.get(name)
            if app is None:
                self.fail(
                    f"Dependency '{name}' is not in the marketplace — submit and merge it "
                    f"in its own PR first, then open this one."
                )
                continue
            if not self._has_compatible_version(app, spec):
                self.fail(
                    f"No marketplace version of '{name}' satisfies '{spec}' — update '{name}' "
                    f"to a compatible version in a separate PR first."
                )

    def _has_compatible_version(self, app: dict, spec: str) -> bool:
        specifier = SpecifierSet(spec)
        for target in app.get("targets", []):
            try:
                if Version(target["version"]) in specifier:
                    return True
            except (InvalidVersion, KeyError):
                continue
        return False
