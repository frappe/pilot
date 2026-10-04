from __future__ import annotations

from typing import NotRequired, TypedDict


class SiteDomain(TypedDict):
    domain: str
    is_site: bool
    is_primary: bool
    public_scheme: str
    tls: bool


class SiteDomains(TypedDict):
    domains: list[SiteDomain]
    primary: str


class DnsRecord(TypedDict):
    type: str
    host: str
    value: str


class DnsRecords(TypedDict):
    """Alternative record sets, each a complete recipe. Empty when no manual records are needed."""

    cname: NotRequired[list[DnsRecord]]
    a: NotRequired[list[DnsRecord]]
