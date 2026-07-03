"""Tests for pilot.core.git_providers — credential storage and URL helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.git_providers import (
    GitCredentialStore,
    GitHubProvider,
    GitProviderError,
    parse_github_owner_repo,
    resolve_app_name_from_repo,
)


def test_github_provider_omits_auth_header_without_token() -> None:
    assert "Authorization" not in GitHubProvider(token="")._headers()


def test_github_provider_sends_auth_header_with_token() -> None:
    assert GitHubProvider(token="ghp_token")._headers()["Authorization"] == "Bearer ghp_token"


def test_credential_store_round_trip(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    assert store.load() is None

    record = store.save("github", "ghp_token", username="octocat")
    assert record["username"] == "octocat"
    assert store.load() == record


def test_credential_store_save_keeps_username_when_omitted(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    store.save("github", "ghp_token", username="octocat")
    updated = store.save("github", "ghp_token_new")
    assert updated["username"] == "octocat"


def test_credential_store_mark_invalid_and_valid(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    store.save("github", "ghp_token")
    store.mark_invalid()
    assert store.load()["is_token_valid"] is False
    store.mark_valid()
    assert store.load()["is_token_valid"] is True


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/frappe/frappe", ("frappe", "frappe")),
        ("https://github.com/frappe/frappe.git", ("frappe", "frappe")),
        ("https://github.com/frappe/frappe/", ("frappe", "frappe")),
    ],
)
def test_parse_github_owner_repo(url: str, expected: tuple[str, str]) -> None:
    assert parse_github_owner_repo(url) == expected


def test_parse_github_owner_repo_rejects_malformed_url() -> None:
    with pytest.raises(GitProviderError):
        parse_github_owner_repo("not-a-url")


def test_resolve_app_name_requires_hooks_file(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\n',
        GitProviderError("myapp/hooks.py not found in repository."),
    ]
    with patch("pilot.core.git_providers.provider_for_repo", return_value=provider):
        with pytest.raises(GitProviderError, match="doesn't look like a Frappe app"):
            resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")


def test_resolve_app_name_succeeds_with_hooks_file(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\ndescription = "A demo app"\n',
        "app_name = 'myapp'\n",
    ]
    with patch("pilot.core.git_providers.provider_for_repo", return_value=provider):
        resolved = resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")
    assert resolved == {"name": "myapp", "description": "A demo app"}


def test_resolve_app_name_defaults_description_when_missing(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\n',
        "app_name = 'myapp'\n",
    ]
    with patch("pilot.core.git_providers.provider_for_repo", return_value=provider):
        resolved = resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")
    assert resolved == {"name": "myapp", "description": ""}
