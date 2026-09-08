#!/usr/bin/env python3
"""Compare marketplace stable release commits to live GitHub branch heads.

Usage:
  ./scripts/compare-marketplace-vs-github.py
  ./scripts/compare-marketplace-vs-github.py /home/frappe/pilot
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def parse_owner_repo(url: str) -> tuple[str, str] | None:
    raw = url.strip().rstrip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "git@github.com:",
    )
    path = ""
    for prefix in prefixes:
        if raw.startswith(prefix):
            path = raw[len(prefix) :]
            break
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def github_branch_head(repo_url: str, branch: str) -> tuple[str, str]:
    parsed = parse_owner_repo(repo_url)
    if not parsed:
        return "", "non_github_repo"
    owner, repo = parsed
    ref = f"refs/heads/{branch}"
    cmd = ["git", "ls-remote", f"https://github.com/{owner}/{repo}", ref]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except subprocess.SubprocessError:
        return "", "ls_remote_failed"
    if result.returncode != 0:
        return "", "ls_remote_failed"
    lines = result.stdout.strip().splitlines()
    if not lines:
        return "", "branch_not_found"
    return lines[0].split("\t")[0].strip(), ""


def load_rows(cache_root: Path) -> list[dict]:
    apps_index = json.loads((cache_root / "apps.json").read_text())
    rows: list[dict] = []
    for app in apps_index:
        name = app.get("name")
        repo = app.get("repo", "")
        rel_file = app.get("releases", "")
        if not name or not repo or not rel_file:
            continue
        payload = json.loads((cache_root / rel_file).read_text())
        releases = payload.get("releases", [])

        # Keep newest stable entry per branch for each app.
        seen_branches: set[str] = set()
        for rel in releases:
            if rel.get("channel") != "stable":
                continue
            branch = rel.get("branch") or ""
            commit = rel.get("commit") or ""
            if not branch or not commit or branch in seen_branches:
                continue
            seen_branches.add(branch)
            rows.append(
                {
                    "name": name,
                    "repo": repo,
                    "branch": branch,
                    "marketplace_version": rel.get("version", ""),
                    "marketplace_commit": commit,
                }
            )
    return rows


def compare_row(row: dict) -> dict:
    github_head, error = github_branch_head(row["repo"], row["branch"])
    status = "error"
    if not error:
        status = "up_to_date" if github_head == row["marketplace_commit"] else "outdated"
    return {
        **row,
        "github_head": github_head,
        "status": status,
        "error": error,
    }


def main() -> int:
    pilot_root = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/frappe/pilot")
    cache_root = pilot_root / "system" / "registry-cache"
    if not (cache_root / "apps.json").exists():
        print(f"apps.json not found under {cache_root}", file=sys.stderr)
        return 1

    rows = load_rows(cache_root)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(compare_row, row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: (item["status"], item["name"], item["branch"]))
    summary = {
        "total_stable_branch_entries_checked": len(results),
        "up_to_date": sum(1 for r in results if r["status"] == "up_to_date"),
        "outdated": sum(1 for r in results if r["status"] == "outdated"),
        "error": sum(1 for r in results if r["status"] == "error"),
    }

    report_path = cache_root / "marketplace-vs-github-report.json"
    report_path.write_text(json.dumps({"summary": summary, "results": results}, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
