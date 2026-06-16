#!/usr/bin/env python3
"""
Orchestrates the marketplace app PR check: find which apps changed
(diff_marketplace_apps.py), then run each one through
run_marketplace_app_check.py. Exits non-zero if any app fails.

Run:
    python3 scripts/check_marketplace_apps.py <old-apps.json> <new-apps.json>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DIFF_SCRIPT = Path(__file__).parent / "diff_marketplace_apps.py"
CHECK_SCRIPT = Path(__file__).parent / "run_marketplace_app_check.py"


def find_changed_apps(old_path: str, new_path: str) -> list[dict]:
    result = subprocess.run(
        ["python3", str(DIFF_SCRIPT), old_path, new_path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def check_app(app: dict) -> bool:
    print(f"\n=== Checking {app['name']} ({app['repo']}@{app['branch']}) ===", flush=True)
    result = subprocess.run(["python3", str(CHECK_SCRIPT), app["repo"], app["branch"]])
    return result.returncode == 0


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: check_marketplace_apps.py <old-apps.json> <new-apps.json>", file=sys.stderr)
        sys.exit(1)

    changed_apps = find_changed_apps(sys.argv[1], sys.argv[2])
    if not changed_apps:
        print("No app code changes detected — nothing to scan.")
        return

    results = {app["name"]: check_app(app) for app in changed_apps}
    failed = [name for name, passed in results.items() if not passed]

    if failed:
        print(f"\nFAILED: {', '.join(failed)} did not pass the marketplace app check.")
        sys.exit(1)

    print(f"\nAll {len(changed_apps)} changed app(s) passed.")


if __name__ == "__main__":
    main()
