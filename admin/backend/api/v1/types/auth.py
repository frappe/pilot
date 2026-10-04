from __future__ import annotations

from typing import NotRequired, TypedDict


class TwoFactorCredential(TypedDict):
    name: str
    confirmed: bool
    confirmed_at: int | None
    created_at: int | None
    last_used_at: int | None


class TwoFactorEnrollment(TypedDict):
    name: str
    secret: str
    provisioning_url: str


class ActiveSession(TypedDict):
    jti: str
    exp: int
    ip: str
    last_seen: int


class AdminSession(TypedDict):
    authenticated: bool
    scope: NotRequired[str]
    two_factor_required: NotRequired[bool]


class TwoFactorStatus(TypedDict):
    enabled: bool
    credentials: list[TwoFactorCredential]
    recovery_codes_remaining: int
    max_devices: int
    recovery_codes: NotRequired[list[str]]


class RecoveryCodes(TypedDict):
    recovery_codes: list[str]


class ActiveSessions(TypedDict):
    active_tokens: list[ActiveSession]
    current_jti: str | None


class RevokedSessions(TypedDict):
    revoked_sessions: int
