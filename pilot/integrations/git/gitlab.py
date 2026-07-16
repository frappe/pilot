from __future__ import annotations

from pilot.integrations.git.base import GitProvider, GitProviderError, inject_https_token


class GitLabProvider(GitProvider):
    """Stub — mapped out for a later phase, not wired up yet."""

    name = "gitlab"
    host = "gitlab.com"
    git_username = "oauth2"

    def validate(self) -> dict:
        raise GitProviderError("GitLab support is not implemented yet.")

    def list_repos(self) -> list[dict]:
        raise GitProviderError("GitLab support is not implemented yet.")

    def authenticated_clone_url(self, repo_url: str) -> str:
        return inject_https_token(repo_url, "oauth2", self.token)

    def list_branches(self, full_name: str) -> list[str]:
        raise GitProviderError("GitLab support is not implemented yet.")
