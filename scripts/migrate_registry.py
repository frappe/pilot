#!/usr/bin/env python3
"""
Migrate apps.json to the new targets-based schema.

Fetches pyproject.toml from GitHub for each app's pilot branches
(version-16, version-15, develop) and builds a targets array with
version, frappe_core, and sibling app dependencies.

Writes output to registry/apps_v2.json (does not overwrite apps.json).

Usage:
    GITHUB_TOKEN=ghp_... python3 scripts/migrate_registry.py
    GITHUB_TOKEN=ghp_... python3 scripts/migrate_registry.py --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion

REGISTRY = Path(__file__).parent.parent / "registry" / "apps.json"
OUTPUT = Path(__file__).parent.parent / "registry" / "apps_v2.json"
PILOT_BRANCHES = ["version-16", "version-15", "develop", "main", "master"]
FRAPPE_KEY = "frappe"
REQUEST_TIMEOUT = 10


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_raw(self, url: str) -> bytes | None:
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (403, 429):
                print("  rate limited — sleeping 60s")
                time.sleep(60)
                return self.fetch_raw(url)
            print(f"  HTTP {error.code}: {url}")
            return None
        except URLError as error:
            print(f"  network error: {error.reason}")
            return None

    def fetch_pyproject(self, repo_url: str, branch: str) -> dict | None:
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/contents/pyproject.toml?ref={branch}"
        raw = self.fetch_raw(url)
        if raw is None:
            return None
        try:
            return tomllib.loads(raw.decode())
        except tomllib.TOMLDecodeError as error:
            print(f"  TOML parse error on {branch}: {error}")
            return None

    def repo_exists(self, repo_url: str) -> bool:
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}"
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT):
                return True
        except HTTPError as error:
            if error.code == 404:
                return False
            if error.code in (403, 429):
                print("  rate limited — sleeping 60s")
                time.sleep(60)
                return self.repo_exists(repo_url)
            return True  # assume exists on other errors
        except URLError:
            return True

    def fetch_dynamic_version(self, repo_url: str, branch: str, app_name: str) -> str | None:
        """Read __version__ from {app_name}/__init__.py for apps using dynamic versioning."""
        owner_repo = repo_url.removeprefix("https://github.com/").rstrip("/")
        url = f"https://api.github.com/repos/{owner_repo}/contents/{app_name}/__init__.py?ref={branch}"
        raw = self.fetch_raw(url)
        if raw is None:
            return None
        for line in raw.decode().splitlines():
            if line.startswith("__version__"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("\"'")


def _is_dynamic_version(toml: dict) -> bool:
    return "version" in toml.get("project", {}).get("dynamic", [])


def parse_target(toml: dict, branch: str, dynamic_version: str | None = None) -> dict | None:
    project = toml.get("project", {})
    version = project.get("version") or dynamic_version
    if not version:
        return None

    bench_deps: dict = (
        toml.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
    )
    frappe_core = bench_deps.get(FRAPPE_KEY)
    dependencies = {k: v for k, v in bench_deps.items() if k != FRAPPE_KEY}

    return {
        "version": version,
        "target_type": "branch",
        "target": branch,
        "frappe_core": frappe_core,
        "dependencies": dependencies,
    }


def sort_key(target: dict) -> Version:
    try:
        return Version(target["version"])
    except InvalidVersion:
        return Version("0")


def build_targets(repo_url: str, available_branches: list[str], client: GitHubClient) -> list[dict]:
    branches = [b for b in PILOT_BRANCHES if b in available_branches]
    targets = []

    for branch in branches:
        print(f"    {branch} ... ", end="", flush=True)
        toml = client.fetch_pyproject(repo_url, branch)
        if toml is None:
            print("not found")
            continue

        dynamic_version = None
        if _is_dynamic_version(toml):
            app_name = toml.get("project", {}).get("name", "")
            dynamic_version = client.fetch_dynamic_version(repo_url, branch, app_name)

        target = parse_target(toml, branch, dynamic_version)
        if target is None:
            print("no version field")
            continue
        targets.append(target)
        print(f"v{target['version']}")

    targets.sort(key=sort_key, reverse=True)
    return targets


def migrate_app(app: dict, client: GitHubClient) -> dict | None:
    repo = app.get("repo")
    if not repo:
        return None

    if not client.repo_exists(repo):
        print("  repo not found — skipping")
        return None

    available = set(app.get("branches", []))
    targets = build_targets(repo, list(available), client)

    return {
        "name": app["name"],
        "title": app["title"],
        "description": app.get("description"),
        "repo": repo,
        "logo_url": app.get("logo_url"),
        "website": app.get("website"),
        "documentation": app.get("documentation"),
        "categories": app.get("categories", []),
        "category": app.get("category"),
        "stars": app.get("stars"),
        "targets": targets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print first 3 results without writing")
    parser.add_argument("--limit", type=int, help="Process only N apps")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set — unauthenticated (60 req/hr limit)")

    apps: list[dict] = json.loads(REGISTRY.read_text())
    if args.limit:
        apps = apps[: args.limit]

    client = GitHubClient(token=token)
    result: list[dict] = []

    skipped = 0
    for index, app in enumerate(apps, 1):
        print(f"[{index}/{len(apps)}] {app['name']}")
        migrated = migrate_app(app, client)
        if migrated is None:
            skipped += 1
        else:
            result.append(migrated)

    with_targets = sum(1 for a in result if a["targets"])

    if args.dry_run:
        print("\n--- sample output (first 3) ---")
        print(json.dumps(result[:3], indent=2, ensure_ascii=False))
        print(f"\n{with_targets}/{len(result)} apps would have targets, {skipped} would be removed")
        return

    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(result)} apps → {OUTPUT}")
    print(f"  {with_targets} with targets, {len(result) - with_targets} without targets")
    print(f"  {skipped} removed (no repo or 404)")


if __name__ == "__main__":
    main()
