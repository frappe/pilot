from __future__ import annotations

import json
import urllib.error
import urllib.request

from pilot.integrations.git.base import (
    GitAuthError,
    GitProvider,
    GitProviderError,
    inject_https_token,
    normalize_to_https,
)


def parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL."""
    url = normalize_to_https(repo_url).rstrip("/").removesuffix(".git")
    parts = url.split("/")
    # Expect ['https:', '', 'github.com', 'owner', 'repo']
    if len(parts) < 5 or not parts[-2] or not parts[-1]:
        raise GitProviderError(f"Cannot parse owner/repo from URL: {repo_url!r}")
    return parts[-2], parts[-1]


class GitHubProvider(GitProvider):
    name = "github"
    host = "github.com"
    api_base = "https://api.github.com"

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bench-cli",
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

    def authenticated_clone_url(self, repo_url: str) -> str:
        return inject_https_token(repo_url, "x-access-token", self.token)

    def list_branches(self, full_name: str) -> list[str]:
        url = f"{self.api_base}/repos/{full_name}/branches?per_page=100"
        data, _ = self._get_json(url, self._headers())
        return [b["name"] for b in data]

    def get_default_branch(self, full_name: str) -> str:
        url = f"{self.api_base}/repos/{full_name}"
        data, _ = self._get_json(url, self._headers())
        return data.get("default_branch", "")

    def fetch_raw_file(self, repo_url: str, path: str, ref: str = "HEAD") -> str:
        owner, repo = parse_github_owner_repo(repo_url)
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        headers = {"User-Agent": "bench"}
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

    def create_repo(self, name: str, description: str, private: bool) -> dict:
        url = f"{self.api_base}/user/repos"
        body = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": False
        }
        headers = {**self._headers(), "Content-Type": "application/json"}
        req = urllib.request.Request(url, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, data=json.dumps(body).encode("utf-8"), timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8")
            if exc.code in (401, 403):
                raise GitAuthError(f"Access denied creating repository: {err_msg}") from exc
            raise GitProviderError(f"HTTP {exc.code} creating repository: {err_msg}") from exc
        except urllib.error.URLError as exc:
            raise GitProviderError(f"Could not reach GitHub: {exc.reason}.") from exc

