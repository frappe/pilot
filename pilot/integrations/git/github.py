from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from pilot.integrations.git.base import (
    GitAuthError,
    GitProvider,
    GitProviderError,
    basic_auth_config,
    normalize_to_https,
)
from pilot.internal.git import git_env

GITHUB_HOST = "github.com"
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL. Both land in an api.github.com path,
    so only a plain owner and repository name are accepted."""
    url = normalize_to_https(repo_url).rstrip("/").removesuffix(".git")
    parts = url.split("/")
    # Expect ['https:', '', 'github.com', 'owner', 'repo']
    if len(parts) < 5 or urllib.parse.urlsplit(url).hostname != GITHUB_HOST:
        raise GitProviderError(f"Cannot parse owner/repo from URL: {repo_url!r}")
    owner, repository = parts[-2], parts[-1]
    if not (_OWNER_REPO_RE.match(owner) and _OWNER_REPO_RE.match(repository)):
        raise GitProviderError(f"Cannot parse owner/repo from URL: {repo_url!r}")
    return owner, repository


class GitHubProvider(GitProvider):
    name = "github"
    host = GITHUB_HOST
    api_base = "https://api.github.com"

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pilot",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def validate(self) -> dict:
        data, _ = self._get_json(f"{self.api_base}/user", self._headers())
        return {"login": data.get("login"), "name": data.get("name")}

    def list_repos(self) -> list[dict]:
        repos: list[dict] = []
        # A couple of pages of the most recently pushed repos is plenty for a
        # picker; the user can always paste a URL for anything older.
        for page in range(1, 4):
            url = (
                f"{self.api_base}/user/repos"
                f"?per_page=100&page={page}&sort=pushed&affiliation=owner,collaborator,organization_member"
            )
            batch, _ = self._get_json(url, self._headers())
            if not batch:
                break
            for r in batch:
                repos.append(
                    {
                        "name": r.get("name"),
                        "full_name": r.get("full_name"),
                        "private": r.get("private", False),
                        "description": r.get("description") or "",
                        "default_branch": r.get("default_branch") or "",
                        "clone_url": r.get("clone_url") or "",
                    }
                )
            if len(batch) < 100:
                break
        return repos

    def auth_config(self, repo_url: str) -> dict[str, str]:
        return basic_auth_config(repo_url, "x-access-token", self.token)

    def list_branches(self, full_name: str) -> list[str]:
        repo_url = f"https://{self.host}/{full_name}"
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", repo_url],
                capture_output=True,
                text=True,
                timeout=30,
                env=git_env(self.auth_config(repo_url)),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitProviderError(f"Timed out listing branches for {full_name}.") from exc
        except OSError as exc:
            raise GitProviderError("Git is required to list repository branches.") from exc
        if result.returncode != 0:
            if "authentication failed" in result.stderr.lower():
                raise GitAuthError(f"GitHub rejected the stored token for {full_name}.")
            raise GitProviderError(f"Could not list branches for {full_name}.")
        return [
            line.split("refs/heads/", 1)[1]
            for line in result.stdout.splitlines()
            if "refs/heads/" in line
        ]

    def has_branch(self, full_name: str, branch: str) -> bool:
        url = f"{self.api_base}/repos/{full_name}/branches/{urllib.parse.quote(branch, safe='')}"
        try:
            self._get_json(url, self._headers())
        except GitProviderError:
            return False
        return True

    def get_default_branch(self, full_name: str) -> str:
        url = f"{self.api_base}/repos/{full_name}"
        data, _ = self._get_json(url, self._headers())
        return data.get("default_branch", "")

    def fetch_raw_file(self, repo_url: str, path: str, ref: str = "HEAD") -> str:
        owner, repo = parse_github_owner_repo(repo_url)
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        headers = {"User-Agent": "pilot"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GitAuthError(f"Access denied reading {path} from repository.") from exc
            if exc.code == 404:
                raise GitProviderError(f"{path} not found in repository.") from exc
            raise GitProviderError(f"HTTP {exc.code} reading {path}.") from exc
        except urllib.error.URLError as exc:
            raise GitProviderError(f"Could not reach GitHub: {exc.reason}.") from exc
