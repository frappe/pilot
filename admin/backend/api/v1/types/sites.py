from __future__ import annotations

from typing import NotRequired, TypedDict


class SiteResource(TypedDict):
    name: str
    exists: bool
    active_apps: list[str]
    framework_branch: str
    broken: bool
    provisioning: bool
    setup_complete: bool


class SiteDetail(SiteResource):
    ssl: bool
    tls: bool
    installable_apps: list[str]
    http_port: int
    nginx_enabled: bool
    admin_tls: bool
    url: str


class WildcardDomains(TypedDict):
    domains: list[str]


class MigrationStarted(TypedDict):
    operation_id: str
    task_id: str


class SiteLoginLink(TypedDict):
    url: str
    hint: NotRequired[str]
