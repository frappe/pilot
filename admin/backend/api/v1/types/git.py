from __future__ import annotations

from typing import NotRequired, TypedDict


class GitConnection(TypedDict):
    connected: bool
    providers: dict[str, str]
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


class GitBranches(TypedDict):
    branches: list[str]
    default_branch: str


class ResolvedApp(TypedDict):
    name: str
    description: str
