"""Provider-agnostic Git integration for private app repositories."""

from __future__ import annotations

from pathlib import Path

from pilot.integrations.git.base import (
    TOKEN_HELP_URLS,
    BranchNotFoundError,
    GitAuthError,
    GitProvider,
    GitProviderError,
    normalize_to_https,
    repo_host,
    without_credentials,
)
from pilot.integrations.git.credentials import CREDENTIALS_FILENAME, GITHUB_APP_AUTH_TYPE, GitCredentialStore
from pilot.integrations.git.github import GitHubProvider, parse_github_owner_repo
from pilot.integrations.git.github_app import GitHubAppGrant, GitHubAppTokens

__all__ = [
    "CREDENTIALS_FILENAME",
    "GITHUB_APP_AUTH_TYPE",
    "TOKEN_HELP_URLS",
    "BranchNotFoundError",
    "GitAuthError",
    "GitCredentialStore",
    "GitHubAppGrant",
    "GitHubAppTokens",
    "GitHubProvider",
    "GitProvider",
    "GitProviderError",
    "auth_config_for",
    "normalize_to_https",
    "parse_github_owner_repo",
    "provider_for_name",
    "provider_for_repo",
    "repo_host",
    "resolve_app_name_from_repo",
    "token_for",
    "without_credentials",
]

PROVIDERS: dict[str, type[GitProvider]] = {
    GitHubProvider.name: GitHubProvider,
}


def provider_for_name(name: str, token: str = "") -> GitProvider:
    cls = PROVIDERS.get((name or "").lower())
    if cls is None:
        raise GitProviderError(f"Unknown git provider: {name!r}.")
    return cls(token)


def provider_for_repo(repo_url: str, token: str = "") -> GitProvider | None:
    """The provider hosting this repo, matched on hostname - never on a substring, which
    would hand github.com.attacker.example a GitHub token."""
    host = repo_host(repo_url)
    for cls in PROVIDERS.values():
        if cls.host and cls.host == host:
            return cls(token)
    return None


def resolve_app_name_from_repo(bench_root: Path, repo_url: str, branch: str = "") -> dict:
    """Return pyproject project name/description from a repo."""
    import tomllib

    provider = provider_for_repo(repo_url, token_for(bench_root, repo_url))
    if provider is None:
        raise GitProviderError(
            f"No supported git provider found for {repo_url!r}. "
            "Only GitHub is supported for automatic app-name resolution."
        )

    requested_branch = branch.strip()
    if requested_branch and not provider.has_branch(
        "/".join(parse_github_owner_repo(repo_url)), requested_branch
    ):
        raise BranchNotFoundError(f"'{requested_branch}' is not a branch of this repository.")

    ref = requested_branch or "HEAD"
    content = provider.fetch_raw_file(repo_url, "pyproject.toml", ref)

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise GitProviderError(f"Could not parse pyproject.toml: {exc}") from exc

    project = data.get("project") or {}
    name = project.get("name", "").strip()
    if not name:
        raise GitProviderError("pyproject.toml does not contain a [project] name field.")
    description = (project.get("description") or "").strip()

    # A Frappe app must ship hooks.py under its importable module.
    try:
        provider.fetch_raw_file(repo_url, f"{name}/hooks.py", ref)
    except GitProviderError as exc:
        raise GitProviderError(
            f"'{name}' doesn't look like a Frappe app (no {name}/hooks.py found)."
        ) from exc

    return {"name": name, "description": description}


def token_for(bench_root: Path, repo_url: str) -> str:
    """The stored PAT, or a Central-minted token under a GitHub App grant. "" when none applies."""
    record = GitCredentialStore(bench_root).load()
    provider = provider_for_repo(repo_url)
    if not record or provider is None or provider.name != record.get("provider"):
        return ""
    if record.get("auth_type") != GITHUB_APP_AUTH_TYPE:
        return record.get("token", "")

    try:
        owner, name = parse_github_owner_repo(repo_url)
    except GitProviderError:
        return ""
    return GitHubAppTokens.get(f"{owner}/{name}")


def auth_config_for(bench_root: Path, repo_url: str) -> dict[str, str]:
    """Git config authenticating this repo when stored credentials apply to its host."""
    token = token_for(bench_root, repo_url)
    provider = provider_for_repo(repo_url, token)
    if not token or provider is None:
        return {}
    return provider.auth_config(repo_url)
