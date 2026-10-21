from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.integrations.central.client import CentralClientError
from pilot.integrations.git import (
    GitCredentialStore,
    GitHubAppGrant,
    GitHubAppTokens,
    auth_config_for,
    token_for,
)

PRIVATE_REPOSITORY = "https://github.com/octo/private-app"
STATUS = {"available": True, "connections": [{"account": "octo", "repositories": []}]}


@pytest.fixture(autouse=True)
def empty_token_cache() -> Iterator[None]:
    GitHubAppTokens.clear()
    yield
    GitHubAppTokens.clear()


def test_central_grant_replaces_the_token_and_mints_repository_tokens(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    store.save("github", "ghp_personal", username="octo")
    minted = {"token": "ghs_repository", "expires_at": "2099-01-01T00:00:00Z"}

    with (
        patch("pilot.integrations.central.client.CentralClient.get_github_status", return_value=STATUS),
        patch(
            "pilot.integrations.central.client.CentralClient.get_github_repository_token", return_value=minted
        ) as mint,
    ):
        GitHubAppGrant(store).get_status()
        first = token_for(tmp_path, PRIVATE_REPOSITORY)
        second = token_for(tmp_path, "git@github.com:octo/private-app.git")

    assert "token" not in store.load()
    assert first == second == "ghs_repository"
    mint.assert_called_once_with("octo/private-app")


def test_a_refused_repository_gets_no_credentials(tmp_path: Path) -> None:
    GitCredentialStore(tmp_path).save_github_app("octo")

    with patch(
        "pilot.integrations.central.client.CentralClient.get_github_repository_token",
        side_effect=CentralClientError("not granted", status_code=403),
    ):
        assert auth_config_for(tmp_path, "https://github.com/octo/public-app") == {}


def test_a_grant_removed_on_central_clears_the_store(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    store.save_github_app("octo")

    with patch(
        "pilot.integrations.central.client.CentralClient.get_github_status",
        return_value={"available": True, "connections": []},
    ):
        GitHubAppGrant(store).get_status()

    assert store.load() is None


def test_a_token_close_to_expiry_is_minted_again(tmp_path: Path) -> None:
    GitCredentialStore(tmp_path).save_github_app("octo")
    expiring = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    minted = [
        {"token": "ghs_expiring", "expires_at": expiring},
        {"token": "ghs_fresh", "expires_at": "2099-01-01T00:00:00Z"},
    ]

    with patch(
        "pilot.integrations.central.client.CentralClient.get_github_repository_token", side_effect=minted
    ):
        assert token_for(tmp_path, PRIVATE_REPOSITORY) == "ghs_expiring"
        assert token_for(tmp_path, PRIVATE_REPOSITORY) == "ghs_fresh"
