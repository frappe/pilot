#!/usr/bin/env python3
"""
Find apps in registry/apps.json whose code reference changed between two
revisions, so only those need a fresh Semgrep scan — not the whole registry.

An app counts as changed if it's new, or if its repo/branch changed. A pure
metadata edit (description, logo_url, category, ...) does not need a re-scan.

Run:
    python3 scripts/diff_marketplace_apps.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_apps(path: Path) -> dict[str, dict]:
    apps = json.loads(path.read_text())
    return {app["name"]: app for app in apps}


def code_reference(app: dict) -> tuple:
    return (app.get("repo"), app.get("branch"))


def find_changed_apps(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> list[dict]:
    changed = []
    for name, app in new_apps.items():
        old_app = old_apps.get(name)
        if old_app is None or code_reference(old_app) != code_reference(app):
            changed.append(app)
    return changed


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: diff_marketplace_apps.py <old-apps.json> <new-apps.json>", file=sys.stderr)
        sys.exit(1)

    old_apps = load_apps(Path(sys.argv[1]))
    new_apps = load_apps(Path(sys.argv[2]))
    changed = find_changed_apps(old_apps, new_apps)

    print(json.dumps(changed, indent=2))


if __name__ == "__main__":
    main()
