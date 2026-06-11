#!/usr/bin/env python3
"""
Import marketplace apps from Frappe Desk CSV exports.

Usage:
    # Step 1 — import Marketplace App metadata (title, description, logo, categories)
    python3 scripts/import_marketplace.py marketplace <path-to-Marketplace-App.csv>

    # Step 2 — import App Source repos and branches
    python3 scripts/import_marketplace.py sources <path-to-App-Source.csv>

    # Run categorize afterwards to assign the 6-category taxonomy:
    python3 scripts/categorize_marketplace.py

Categories (6):
    Applications, Extensions, Integrations, Compliance, Developer Tools, Utilities
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CLOUD_BASE = "https://cloud.frappe.io"
REGISTRY = Path(__file__).parent.parent / "registry" / "apps.json"
STABLE = re.compile(r"^(develop|main|master|version-\d+(?:\.\d+)?)$")

VERSION_TO_BRANCH = {
    "nightly": "develop",
    "version 12": "version-12",
    "version 13": "version-13",
    "version 14": "version-14",
    "version 15": "version-15",
    "version 16": "version-16",
}


def _branch(version: str) -> str | None:
    return VERSION_TO_BRANCH.get(version.strip().lower())


def _abs_url(path: str) -> str | None:
    if not path:
        return None
    return path if path.startswith("http") else f"{CLOUD_BASE}{path}"


def _pick_default_branch(branches: list[str]) -> str | None:
    version_branches = sorted(
        [b for b in branches if re.match(r"^version-\d+", b)],
        key=lambda b: [int(x) for x in re.findall(r"\d+", b)],
        reverse=True,
    )
    if version_branches:
        return version_branches[0]
    for preferred in ("develop", "main", "master"):
        if preferred in branches:
            return preferred
    return branches[0] if branches else None


def cmd_marketplace(csv_path: Path) -> None:
    """Import Marketplace App metadata (title, description, logo, categories)."""
    existing: dict[str, dict] = {}
    if REGISTRY.exists():
        for app in json.loads(REGISTRY.read_text()):
            existing[app["name"]] = app

    grouped: dict[str, list[dict]] = defaultdict(list)
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row.get("ID", "").strip()
            status = row.get("Status", "").strip()
            if name and status == "Published":
                grouped[name].append(row)

    cloud_apps: list[dict] = []
    for name, rows in grouped.items():
        first = rows[0]
        versions = sorted({
            r.get("Version (Marketplace App Version)", "").strip()
            for r in rows
            if r.get("Version (Marketplace App Version)", "").strip()
        })
        cloud_apps.append({
            "name": name,
            "title": first.get("Title", "").strip(),
            "description": first.get("Description", "").strip() or None,
            "website": first.get("Website", "").strip() or None,
            "documentation": first.get("Documentation", "").strip() or None,
            "categories": sorted({
                r.get("Category (Marketplace App Categories)", "").strip()
                for r in rows if r.get("Category (Marketplace App Categories)", "").strip()
            }),
            "logo_url": _abs_url(first.get("Image", "").strip()),
            "_versions": versions,
        })

    seen: set[str] = set()
    result: list[dict] = []
    for ca in cloud_apps:
        name = ca["name"]
        seen.add(name)
        lo = existing.get(name, {})
        local_branches: list[str] = lo.get("branches", [])
        if not local_branches:
            local_branches = [b for v in ca["_versions"] if (b := _branch(v))]
        result.append({
            "name": name,
            "title": ca["title"] or lo.get("title", name),
            "description": ca["description"] or lo.get("description"),
            "repo": lo.get("repo"),
            "branch": lo.get("branch"),
            "branches": local_branches,
            "logo_url": ca["logo_url"] or lo.get("logo_url"),
            "website": ca["website"],
            "documentation": ca["documentation"],
            "categories": ca["categories"],
            "category": lo.get("category"),
            "stars": lo.get("stars"),
        })

    for name, lo in existing.items():
        if name not in seen:
            result.append(lo)

    REGISTRY.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(result)} apps to {REGISTRY}")
    print(f"  {sum(1 for a in result if a.get('repo'))} installable, "
          f"{sum(1 for a in result if a.get('description'))} have description")


def cmd_sources(csv_path: Path) -> None:
    """Import App Source repos and branches."""
    by_app_repo: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            app, repo, branch = row.get("App", ""), row.get("Repository URL", ""), row.get("Branch", "")
            if app and repo and branch:
                by_app_repo[app][repo].append(branch)

    canonical: dict[str, dict] = {}
    for app, repos in by_app_repo.items():
        frappe = {r: b for r, b in repos.items() if "github.com/frappe/" in r}
        src = frappe if frappe else repos
        repo = max(src, key=lambda r: len(src[r]))
        branches = sorted({b for b in src[repo] if STABLE.match(b)})
        if repo and branches:
            canonical[app] = {"repo": repo, "branches": branches,
                               "branch": _pick_default_branch(branches)}

    apps = json.loads(REGISTRY.read_text())
    updated = 0
    for app in apps:
        name = app["name"]
        if name in canonical:
            src = canonical[name]
            if not app.get("repo"):
                app["repo"] = src["repo"]
                app["branch"] = src["branch"]
                updated += 1
            app["branches"] = src["branches"]  # always refresh branches

    REGISTRY.write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n")
    installable = sum(1 for a in apps if a.get("repo"))
    print(f"Repos added: {updated}")
    print(f"Total installable apps: {installable}/{len(apps)}")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, path_arg = sys.argv[1], Path(sys.argv[2])
    if not path_arg.exists():
        print(f"File not found: {path_arg}")
        sys.exit(1)
    if cmd == "marketplace":
        cmd_marketplace(path_arg)
    elif cmd == "sources":
        cmd_sources(path_arg)
    else:
        print(f"Unknown command: {cmd}. Use 'marketplace' or 'sources'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
