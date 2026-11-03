from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class SiteDomain(TypedDict):
    domain: str
    is_site: bool
    is_primary: bool
    public_scheme: str
    tls: bool
    redirect_to_primary: bool
    certificate: Literal["not_required", "issued", "pending", "missing"]


class SiteDomains(TypedDict):
    domains: list[SiteDomain]
    primary: str
    certificate_email_required: NotRequired[bool]
    certificate_pending: NotRequired[bool]


class DnsRecord(TypedDict):
    type: str
    host: str
    value: str


class DnsRecords(TypedDict):
    """Alternative record sets, each a complete recipe. Empty when no manual records are needed."""

    cname: NotRequired[list[DnsRecord]]
    a: NotRequired[list[DnsRecord]]


class DnsCheck(TypedDict):
    matched: bool
    message: str
