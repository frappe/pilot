from __future__ import annotations

from typing import NotRequired, TypedDict


class GitHubAppAccount(TypedDict):
    account: str
    repositories: list[str]


class CentralGitHub(TypedDict):
    available: bool
    connections: list[GitHubAppAccount]
    error: NotRequired[str]


class GitConnection(TypedDict):
    connected: bool
    providers: dict[str, str]
    auth_type: NotRequired[str]
    central: NotRequired[CentralGitHub]
    provider: NotRequired[str | None]
    username: NotRequired[str]
    token_preview: NotRequired[str]
    is_token_valid: NotRequired[bool]
    token_expires_at: NotRequired[str | None]


class GitRepository(TypedDict):
    name: str
    full_name: str
    private: bool
    description: str
    default_branch: str
    clone_url: str


class GitConnectionAttempt(TypedDict):
    url: str


class GitBranches(TypedDict):
    branches: list[str]
    default_branch: str


class ResolvedApp(TypedDict):
    name: str
    description: str
