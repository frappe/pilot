from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_HEADER = {"alg": "HS256", "typ": "JWT"}
DEFAULT_TTL = 24 * 3600
LOGIN_TTL = 5 * 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()


def issue_token(
    secret: str,
    ttl: int = DEFAULT_TTL,
    issued_at: float | None = None,
    jti: str | None = None,
    scope: str = "bench",
    site: str | None = None,
) -> str:
    if not secret:
        raise ValueError("JWT secret is not configured.")
    now = int(issued_at or time.time())
    payload = {"sub": "admin", "iat": now, "exp": now + ttl, "scope": scope}
    if jti:
        payload["jti"] = jti
    if site:
        payload["site"] = site
    body = ".".join(
        _b64(json.dumps(part, separators=(",", ":")).encode()) for part in (_HEADER, payload)
    )
    return f"{body}.{_b64(_sign(body, secret))}"


def decode_token(token: str, secret: str) -> dict | None:
    """Return the token's claims if its signature is valid and it has not
    expired, else None."""
    if not token or not secret:
        return None
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        if not hmac.compare_digest(_unb64(signature_b64), _sign(f"{header_b64}.{payload_b64}", secret)):
            return None
        payload = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    return payload if isinstance(exp, int) and time.time() < exp else None


def verify_token(token: str, secret: str) -> bool:
    return decode_token(token, secret) is not None


def has_scope(claims: dict | None, site: str) -> bool:
    if not claims:
        return False
    token_scope = claims.get("scope")
    if token_scope == "bench":
        return True
    if token_scope == "site":
        return claims.get("site") == site
    return False


def issue_login_token(secret: str) -> str:
    """A short-lived, single-use token for the ?sid= sign-in link."""
    return issue_token(secret, ttl=LOGIN_TTL, jti=secrets.token_urlsafe(8), scope="bench")


def issue_site_token(secret: str, site: str, ttl: int = DEFAULT_TTL) -> str:
    """A token scoped to a single site for site-to-bench API calls."""
    if not site:
        raise ValueError("Site name is required.")
    return issue_token(secret, ttl=ttl, scope="site", site=site)


def ensure_jwt_secret(toml_path) -> str:
    from pilot.config.toml_store import BenchTomlStore

    store = BenchTomlStore(toml_path)
    data = store.read_raw()
    secret = data.get("admin", {}).get("jwt_secret")
    if secret:
        return secret
    secret = secrets.token_urlsafe(32)
    data.setdefault("admin", {})["jwt_secret"] = secret
    store.write_raw(data)
    return secret


class GenerateSessionCommand(Command):
    name = "generate-admin-session"
    help = "Issue a 5-minute one-time sign-in token (use --full-path for a sign-in URL)."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--full-path", action="store_true",
                            help="Print the full admin URL with ?sid= instead of the bare token.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, full_path=args.full_path)

    def __init__(self, bench: "Bench", full_path: bool = False) -> None:
        self.bench = bench
        self.full_path = full_path

    def run(self) -> None:
        from pilot.admin_url import admin_url

        if not self.bench.config.admin.password:
            raise BenchError("Admin has no password set; configure [admin].password in bench.toml first.")
        token = issue_login_token(self._jwt_secret())
        if self.full_path:
            print(f"{admin_url(self.bench.config)}/?sid={urllib.parse.quote(token, safe='')}")
        else:
            print(token)

    def _jwt_secret(self) -> str:
        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        self.bench.config.admin.jwt_secret = secret
        return secret


class IssueSiteTokenCommand(Command):
    name = "issue-site-token"
    help = "Issue a scoped JWT for site-to-bench API calls."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name to scope the token to.")
        parser.add_argument("--ttl", type=int, default=DEFAULT_TTL,
                            help="Token TTL in seconds (default: 86400).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site, ttl=args.ttl)

    def __init__(self, bench: "Bench", site: str, ttl: int = DEFAULT_TTL) -> None:
        self.bench = bench
        self.site = site
        self.ttl = ttl

    def run(self) -> None:
        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        self.bench.config.admin.jwt_secret = secret
        print(issue_site_token(secret, self.site, ttl=self.ttl))
