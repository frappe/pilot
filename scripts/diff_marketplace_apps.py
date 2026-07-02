#!/usr/bin/env python3
"""
Find targets in registry/apps_v2.json that changed between two revisions,
so only those need a fresh scan — not the whole registry.

Only the targets that actually changed are emitted, so callers re-scan
the minimum. Targets are diffed as a list with difflib rather than matched
up by "version", since a target's version is not guaranteed unique within
an app (e.g. multiple branches can share the same version); the added and
replaced targets on the new side are the changed ones.

If the app is new or its repo changed, the code location itself moved, so
every target is emitted even when the entries are textually identical.

Output: JSON list of {name, repo, target_type, target} items.

Run:
    python3 scripts/diff_marketplace_apps.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path


def load_apps(path: Path) -> dict[str, dict]:
    apps = json.loads(path.read_text())
    return {app["name"]: app for app in apps}


def target_lines(targets: list[dict]) -> list[str]:
    return [json.dumps(t, sort_keys=True) for t in targets]


def changed_targets(old_app: dict, app: dict) -> list[dict]:
    new_targets = app.get("targets", [])
    matcher = difflib.SequenceMatcher(
        a=target_lines(old_app.get("targets", [])),
        b=target_lines(new_targets),
        autojunk=False,
    )
    changed = []
    for tag, _, _, start, end in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            changed.extend(new_targets[start:end])
    return changed


def find_changed_targets(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> list[dict]:
    changed = []
    for name, app in new_apps.items():
        old_app = old_apps.get(name)
        if old_app is None or old_app.get("repo") != app.get("repo"):
            targets = app.get("targets", [])
        else:
            targets = changed_targets(old_app, app)
        changed.extend({"name": name, "repo": app["repo"], **t} for t in targets)

    return changed


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: diff_marketplace_apps.py <old-apps.json> <new-apps.json>", file=sys.stderr)
        sys.exit(1)

    old_apps = load_apps(Path(sys.argv[1]))
    new_apps = load_apps(Path(sys.argv[2]))
    changed = find_changed_targets(old_apps, new_apps)

    print(json.dumps(changed, indent=2))


if __name__ == "__main__":
    main()
