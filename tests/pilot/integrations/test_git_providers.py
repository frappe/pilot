"""Tests for pilot.integrations.git - credential storage and URL helpers."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.integrations.git import (
    GitCredentialStore,
    GitHubProvider,
    GitProviderError,
    parse_github_owner_repo,
    resolve_app_name_from_repo,
)
from pilot.integrations.git.base import GitAuthError


def test_github_provider_omits_auth_header_without_token() -> None:
    assert "Authorization" not in GitHubProvider(token="")._headers()


def test_github_provider_sends_auth_header_with_token() -> None:
    assert GitHubProvider(token="ghp_token")._headers()["Authorization"] == "Bearer ghp_token"


def _ls_remote(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_list_branches_returns_every_head_from_one_ls_remote() -> None:
    stdout = (
        "aaa\trefs/heads/develop\n"
        "bbb\trefs/heads/l10n_version-16-hotfix\n"
        "ccc\trefs/heads/mergify/bp/version-13-hotfix/pr-26107\n"
        "ddd\trefs/heads/version-16-hotfix\n"
    )
    with patch(
        "pilot.integrations.git.github.subprocess.run", return_value=_ls_remote(stdout=stdout)
    ) as run:
        branches = GitHubProvider(token="").list_branches("frappe/erpnext")
    assert branches == [
        "develop",
        "l10n_version-16-hotfix",
        "mergify/bp/version-13-hotfix/pr-26107",
        "version-16-hotfix",
    ]
    assert run.call_args.args[0][:3] == ["git", "ls-remote", "--heads"]


def test_list_branches_offers_the_token_as_a_scoped_header() -> None:
    with patch(
        "pilot.integrations.git.github.subprocess.run", return_value=_ls_remote()
    ) as run:
        GitHubProvider(token="ghp_token").list_branches("acme/private")
    env = run.call_args.kwargs["env"]
    values = [env[key] for key in env if key.startswith("GIT_CONFIG_VALUE_")]
    assert any(value.startswith("Authorization: Basic ") for value in values)
    assert "ghp_token" not in run.call_args.args[0]


def test_list_branches_maps_a_rejected_token_to_auth_error() -> None:
    failed = _ls_remote(returncode=128, stderr="fatal: Authentication failed for 'https://github.com/acme/private/'")
    with (
        patch("pilot.integrations.git.github.subprocess.run", return_value=failed),
        pytest.raises(GitAuthError),
    ):
        GitHubProvider(token="ghp_expired").list_branches("acme/private")


def test_list_branches_wraps_other_git_failures() -> None:
    missing = _ls_remote(returncode=128, stderr="fatal: repository 'https://github.com/acme/gone/' not found")
    with (
        patch("pilot.integrations.git.github.subprocess.run", return_value=missing),
        pytest.raises(GitProviderError),
    ):
        GitHubProvider(token="").list_branches("acme/gone")


def test_list_branches_wraps_a_timeout() -> None:
    timeout = subprocess.TimeoutExpired(cmd=["git"], timeout=30)
    with (
        patch("pilot.integrations.git.github.subprocess.run", side_effect=timeout),
        pytest.raises(GitProviderError),
    ):
        GitHubProvider(token="").list_branches("acme/slow")


def test_list_branches_wraps_a_missing_git_executable() -> None:
    with (
        patch("pilot.integrations.git.github.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(GitProviderError, match="Git is required"),
    ):
        GitHubProvider(token="").list_branches("acme/repo")


def test_stored_token_travels_as_git_config_not_in_the_url(tmp_path: Path) -> None:
    """A token in the clone URL lands in argv (/proc) and in .git/config; a header
    scoped to the host does neither."""
    from pilot.integrations.git import auth_config_for
    from pilot.integrations.git.credentials import GitCredentialStore

    GitCredentialStore(tmp_path).save("github", "ghp_secret")
    config = auth_config_for(tmp_path, "https://github.com/frappe/erpnext")

    assert list(config) == ["http.https://github.com/.extraHeader"]
    assert config["http.https://github.com/.extraHeader"].startswith("Authorization: Basic ")
    assert "ghp_secret" not in str(config)


def test_stored_token_is_not_offered_to_a_lookalike_host(tmp_path: Path) -> None:
    from pilot.integrations.git import auth_config_for
    from pilot.integrations.git.credentials import GitCredentialStore

    GitCredentialStore(tmp_path).save("github", "ghp_secret")

    assert auth_config_for(tmp_path, "https://github.com.attacker.example/frappe/erpnext") == {}
    assert auth_config_for(tmp_path, "https://notgithub.com/frappe/erpnext") == {}


def test_git_env_disables_the_ext_transport() -> None:
    from pilot.internal.git import git_env

    env = git_env()

    settings = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert settings["protocol.ext.allow"] == "never"


def test_credential_store_round_trip(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    assert store.load() is None

    record = store.save("github", "ghp_token", username="octocat")
    assert record["username"] == "octocat"
    assert store.load() == record


def test_credential_store_keeps_token_file_private(tmp_path: Path) -> None:
    store = GitCredentialStore(tmp_path)
    store.save("github", "ghp_token")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    store.path.chmod(0o644)
    store.mark_invalid()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


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


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "https://evil.example.com/frappe/frappe",
        "https://github.com/frappe/..%2f..%2fuser",
        "https://github.com/frappe/repo?x=1",
    ],
)
def test_parse_github_owner_repo_rejects_anything_but_a_github_owner_repo(url: str) -> None:
    with pytest.raises(GitProviderError):
        parse_github_owner_repo(url)


def test_resolve_app_name_requires_hooks_file(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\n',
        GitProviderError("myapp/hooks.py not found in repository."),
    ]
    with (
        patch("pilot.integrations.git.provider_for_repo", return_value=provider),
        pytest.raises(GitProviderError, match="doesn't look like a Frappe app"),
    ):
        resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")


def test_resolve_app_name_succeeds_with_hooks_file(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\ndescription = "A demo app"\n',
        "app_name = 'myapp'\n",
    ]
    with patch("pilot.integrations.git.provider_for_repo", return_value=provider):
        resolved = resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")
    assert resolved == {"name": "myapp", "description": "A demo app"}


def test_resolve_app_name_defaults_description_when_missing(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.fetch_raw_file.side_effect = [
        '[project]\nname = "myapp"\n',
        "app_name = 'myapp'\n",
    ]
    with patch("pilot.integrations.git.provider_for_repo", return_value=provider):
        resolved = resolve_app_name_from_repo(tmp_path, "https://github.com/acme/myapp")
    assert resolved == {"name": "myapp", "description": ""}
