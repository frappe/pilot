#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: find which targets changed
(diff_marketplace_apps.py), clone each once, then run every validator
(semgrep, app quality, dependencies) against the clone.
Exits non-zero if any target fails any validator.

Run:
    python3 scripts/check_marketplace_apps.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from clone_utils import clone_app
from diff_marketplace_apps import find_changed_targets, load_apps
from run_app_validations import AppValidator
from run_dependency_validations import DependencyValidator
from run_semgrep_validations import SemgrepValidator
from validate_registry_schema import SchemaValidator


def validators_for(target: dict, clone_dir: Path, marketplace: dict[str, dict]) -> list:
    repo, ref = target["repo"], target["target"]
    return [
        SemgrepValidator(clone_dir, f"{repo}@{ref}"),
        AppValidator(repo, clone_dir),
        DependencyValidator(target.get("dependencies", {}), marketplace),
    ]


def apps_missing_targets(old_apps: dict[str, dict], new_apps: dict[str, dict]) -> list[str]:
    """New or edited apps that declare no targets — the target-driven scan can't
    see them (they produce zero targets), so they'd slip through unchecked."""
    return [
        name
        for name, app in new_apps.items()
        if old_apps.get(name) != app and not app.get("targets")
    ]


def check_target(target: dict, marketplace: dict[str, dict]) -> bool:
    print(f"\n=== Checking {target['name']} ({target.get('repo')}@{target.get('target')}) ===", flush=True)

    if not SchemaValidator(target).run():
        return False

    repo, ref, target_type = target["repo"], target["target"], target["target_type"]
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        try:
            clone_app(repo, ref, target_type, clone_dir)
        except RuntimeError as exc:
            print(f"  FAIL: {exc}")
            return False

        results = [validator.run() for validator in validators_for(target, clone_dir, marketplace)]

    return all(results)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: check_marketplace_apps.py <old-apps.json> <new-apps.json>", file=sys.stderr)
        sys.exit(1)

    marketplace = load_apps(Path(sys.argv[1]))
    new_apps = load_apps(Path(sys.argv[2]))

    missing_targets = apps_missing_targets(marketplace, new_apps)
    if missing_targets:
        print(f"\nFAILED: {', '.join(missing_targets)} has no targets — add at least one target.")
        sys.exit(1)

    changed_targets = find_changed_targets(marketplace, new_apps)

    if not changed_targets:
        print("No app code changes detected — nothing to scan.")
        return

    results = {f"{t['name']}@{t['target']}": check_target(t, marketplace) for t in changed_targets}
    failed = [key for key, passed in results.items() if not passed]

    if failed:
        print(f"\nFAILED: {', '.join(failed)} did not pass the marketplace checks.")
        sys.exit(1)

    print(f"\nAll {len(changed_targets)} changed target(s) passed.")


if __name__ == "__main__":
    main()
