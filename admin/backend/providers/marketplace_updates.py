from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from pilot.core.registry_cache import RegistryCache
from pilot.integrations.git.github import parse_github_owner_repo
from pilot.utils import run_command


@dataclass
class MarketplaceGitHubUpdates:
    cli_root: Path

    def check(self, *, refresh: bool) -> dict:
        cache = RegistryCache(self.cli_root)
        if refresh:
            self._force_refresh(cache)

        apps = cache.load()
        rows = self._release_rows(cache, apps)

        compared: list[dict] = []
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(self._compare_row, row) for row in rows]
            for future in as_completed(futures):
                compared.append(future.result())

        compared.sort(key=lambda item: (item["status"], item["name"], item["branch"]))
        summary = {
            "total": len(compared),
            "up_to_date": sum(1 for item in compared if item["status"] == "up_to_date"),
            "outdated": sum(1 for item in compared if item["status"] == "outdated"),
            "error": sum(1 for item in compared if item["status"] == "error"),
        }
        return {"summary": summary, "apps": compared}

    @staticmethod
    def _force_refresh(cache: RegistryCache) -> None:
        # Fetch and hard-reset to remote HEAD so every JSON file is refreshed now.
        run_command(["git", "-C", str(cache.path), "fetch", "--depth", "1", "origin", "HEAD"])
        run_command(["git", "-C", str(cache.path), "reset", "--hard", "FETCH_HEAD"])

    @staticmethod
    def _release_rows(cache: RegistryCache, apps: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for app in apps:
            name = app.get("name")
            repo = app.get("repo", "")
            if not name or not repo:
                continue
            releases = cache.releases(name)
            seen_branches: set[str] = set()
            for release in releases:
                if release.get("channel") != "stable":
                    continue
                branch = (release.get("branch") or "").strip()
                commit = (release.get("commit") or "").strip()
                if not branch or not commit or branch in seen_branches:
                    continue
                seen_branches.add(branch)
                rows.append(
                    {
                        "name": name,
                        "repo": repo,
                        "branch": branch,
                        "marketplace_version": release.get("version") or "",
                        "marketplace_commit": commit,
                    }
                )
        return rows

    def _compare_row(self, row: dict) -> dict:
        head, error = self._github_branch_head(row["repo"], row["branch"])
        status = "error"
        if not error:
            status = "up_to_date" if head == row["marketplace_commit"] else "outdated"
        return {
            **row,
            "github_head": head,
            "status": status,
            "error": error,
        }

    @staticmethod
    def _github_branch_head(repo_url: str, branch: str) -> tuple[str, str]:
        try:
            owner, repo = parse_github_owner_repo(repo_url)
        except Exception:
            return "", "non_github_repo"

        remote = f"https://github.com/{owner}/{repo}"
        ref = f"refs/heads/{branch}"
        try:
            result = run_command(["git", "ls-remote", remote, ref], timeout=20)
        except Exception:
            return "", "ls_remote_failed"

        stdout = result.stdout.decode().strip().splitlines()
        if not stdout:
            return "", "branch_not_found"

        sha = stdout[0].split("\t", 1)[0].strip()
        if not sha:
            return "", "branch_not_found"
        return sha, ""
