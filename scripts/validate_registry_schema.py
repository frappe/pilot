#!/usr/bin/env python3
"""
Validate the structural integrity of apps that are new or changed in
registry/apps_v2.json — catches malformed entries (e.g. a new app added
with no targets) early in CI, before any clone/scan work runs.

Only apps that differ from the base revision are checked, so pre-existing
registry entries left untouched by a PR don't fail unrelated checks.

Checks per new/changed app:
  1. "name" and "repo" are present
  2. "targets" is a non-empty list
  3. each target has "version", "target_type", and "target"

Run:
    python3 scripts/validate_registry_schema.py <old-apps_v2.json> <new-apps_v2.json>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validator import Validator

REQUIRED_TARGET_FIELDS = ("version", "target_type", "target")


class SchemaValidator(Validator):
    name = "schema"

    def __init__(self, target: dict):
        super().__init__()
        self.target = target

    def validate(self) -> None:
        for field in REQUIRED_TARGET_FIELDS:
            if not self.target.get(field):
                self.fail(f"target missing '{field}'")

