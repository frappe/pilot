from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class TlsStatus(TypedDict):
    state: Literal["valid", "pending", "failed", "expiring", "expired"]
    expires_at: str | None
    days_left: int | None
    error: str | None
    checked_at: str | None


class SiteDomain(TypedDict):
    domain: str
    is_site: bool
    is_primary: bool
    public_scheme: str
    tls: bool
    tls_status: TlsStatus | None


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
