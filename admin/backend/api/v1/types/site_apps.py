from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class SiteApp(TypedDict):
    name: str
    title: str
    description: str
    branch: str
    commit: str
    version: str
    repo: str
    has_local_changes: NotRequired[bool]


class SiteApps(TypedDict):
    apps: list[SiteApp]
    can_disable: bool


class EnabledApp(TypedDict):
    app: str
    enabled: Literal[True]


class DisabledApp(TypedDict):
    app: str
    disabled: Literal[True]
