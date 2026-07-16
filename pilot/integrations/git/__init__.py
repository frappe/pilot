"""Provider-agnostic Git integration for cloning private app repositories.

Only GitHub is fully implemented; GitLabProvider is a stub that maps out the
same surface for a later phase. This package's public surface is re-exported
here so callers do `from pilot.integrations.git import ...` rather than
reaching into base/credentials/github/gitlab directly.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from pilot.integrations.git.base import (
    TOKEN_HELP_URLS,
    GitAuthError,
    GitProvider,
    GitProviderError,
    inject_https_token,
    normalize_to_https,
)
from pilot.integrations.git.credentials import CREDENTIALS_FILENAME, GitCredentialStore
from pilot.integrations.git.github import GitHubProvider, parse_github_owner_repo
from pilot.integrations.git.gitlab import GitLabProvider

__all__ = [
    "CREDENTIALS_FILENAME",
    "TOKEN_HELP_URLS",
    "GitAuthError",
    "GitCredentialStore",
    "GitHubProvider",
    "GitLabProvider",
    "GitProvider",
    "GitProviderError",
    "authenticated_url_for",
    "git_remote_for",
    "inject_https_token",
    "normalize_to_https",
    "parse_github_owner_repo",
    "provider_for_name",
    "provider_for_repo",
    "resolve_app_name_from_repo",
]

PROVIDERS: dict[str, type[GitProvider]] = {
    GitHubProvider.name: GitHubProvider,
    GitLabProvider.name: GitLabProvider,
}


def provider_for_name(name: str, token: str = "") -> GitProvider:
    cls = PROVIDERS.get((name or "").lower())
    if cls is None:
        raise GitProviderError(f"Unknown git provider: {name!r}.")
    return cls(token)


def provider_for_repo(repo_url: str, token: str = "") -> GitProvider | None:
    for cls in PROVIDERS.values():
        if cls.host and cls.host in (repo_url or ""):
            return cls(token)
    return None


def resolve_app_name_from_repo(bench_root: Path, repo_url: str, branch: str = "") -> dict:
    """Fetch *pyproject.toml* from *repo_url* and return its ``project`` name/description.

    Uses the stored credential when available so private repos work.
    Public repos are fetched anonymously when no token is on file.
    """
    import tomllib

    record = GitCredentialStore(bench_root).load()
    token = record.get("token", "") if record else ""

    provider = provider_for_repo(repo_url, token)
    if provider is None:
        raise GitProviderError(
            f"No supported git provider found for {repo_url!r}. "
            "Only GitHub is supported for automatic app-name resolution."
        )

    ref = branch.strip() or "HEAD"
    content = provider.fetch_raw_file(repo_url, "pyproject.toml", ref)

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise GitProviderError(f"Could not parse pyproject.toml: {exc}") from exc

    project = data.get("project") or {}
    name = project.get("name", "").strip()
    if not name:
        raise GitProviderError(
            "pyproject.toml does not contain a [project] name field."
        )
    description = (project.get("description") or "").strip()

    # Every Frappe app ships a hooks.py at <module>/hooks.py — bench itself
    # uses this file to recognize an app. A repo without one is just a
    # regular Python package, not something Frappe can install.
    try:
        provider.fetch_raw_file(repo_url, f"{name}/hooks.py", ref)
    except GitProviderError as exc:
        raise GitProviderError(f"'{name}' doesn't look like a Frappe app (no {name}/hooks.py found).") from exc

    return {"name": name, "description": description}


def authenticated_url_for(bench_root: Path, repo_url: str) -> str:
    """Return the credential-free URL used for Git transport."""
    return git_remote_for(bench_root, repo_url)[0]


def git_remote_for(bench_root: Path, repo_url: str) -> tuple[str, dict | None]:
    """Return a remote URL and optional Git auth environment."""
    record = GitCredentialStore(bench_root).load()
    if not record or not record.get("token"):
        return repo_url, None
    provider = provider_for_repo(repo_url, record["token"])
    if provider is None or provider.name != record.get("provider"):
        return repo_url, None

    credentials = f"{provider.git_username}:{record['token']}".encode()
    authorization = base64.b64encode(credentials).decode()
    env = os.environ.copy()
    index = int(env.get("GIT_CONFIG_COUNT", "0"))
    env["GIT_CONFIG_COUNT"] = str(index + 1)
    env[f"GIT_CONFIG_KEY_{index}"] = "http.extraHeader"
    env[f"GIT_CONFIG_VALUE_{index}"] = f"Authorization: Basic {authorization}"
    return normalize_to_https(repo_url), env
