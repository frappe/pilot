from __future__ import annotations

from typing import Literal, TypedDict


class MarketplaceApp(TypedDict):
    name: str
    repo: str
    branch: str
    commit: str
    channel: Literal["stable", "nightly"]
    version: str
    frappe_version: str
    required_version: str
    dependencies: dict[str, str]
    is_installable: bool
    title: str
    description: str
    logo_url: str
    category: str
    categories: list[str]
    stars: int | None
    documentation: str
    website: str
