#!/usr/bin/env python3
"""Fetch GitHub star counts for apps with repos and store in registry/apps.json."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REGISTRY = Path(__file__).parent.parent / "registry" / "apps.json"


def get_stars(repo_url: str) -> int | None:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", repo_url)
    if not m:
        return None
    owner, repo = m.groups()
    r = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}", "--jq", ".stargazers_count"],
        capture_output=True,
        text=True,
    )
    val = r.stdout.strip()
    return int(val) if r.returncode == 0 and val.isdigit() else None


def main() -> None:
    apps = json.loads(REGISTRY.read_text())
    updated = 0
    for app in apps:
        if app.get("repo"):
            stars = get_stars(app["repo"])
            if stars is not None:
                app["stars"] = stars
                updated += 1
                print(f"  {app['name']}: {stars:,}")

    REGISTRY.write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n")
    print(f"\nUpdated {updated} apps in {REGISTRY}")


if __name__ == "__main__":
    main()
