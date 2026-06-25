from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import time
import tomllib
import urllib.parse
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError
from bench_cli.utils import write_toml

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench

_HEADER = {"alg": "HS256", "typ": "JWT"}
DEFAULT_TTL = 24 * 3600


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()


def issue_token(secret: str, ttl: int = DEFAULT_TTL, issued_at: float | None = None) -> str:
    if not secret:
        raise ValueError("JWT secret is not configured.")
    now = int(issued_at or time.time())
    body = ".".join(
        _b64(json.dumps(part, separators=(",", ":")).encode())
        for part in (_HEADER, {"sub": "admin", "iat": now, "exp": now + ttl})
    )
    return f"{body}.{_b64(_sign(body, secret))}"


def verify_token(token: str, secret: str) -> bool:
    if not token or not secret:
        return False
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        if not hmac.compare_digest(_unb64(signature_b64), _sign(f"{header_b64}.{payload_b64}", secret)):
            return False
        exp = json.loads(_unb64(payload_b64)).get("exp")
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(exp, int) and time.time() < exp


class GenerateSessionCommand(Command):
    name = "generate-session"
    help = "Issue a 1-day admin session token (use --full-path for a sign-in URL)."

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
        from bench_cli.admin_url import admin_url

        if not self.bench.config.admin.password:
            raise BenchError("Admin has no password set; configure [admin].password in bench.toml first.")
        token = issue_token(self._jwt_secret())
        if self.full_path:
            print(f"{admin_url(self.bench.config)}/?sid={urllib.parse.quote(token, safe='')}")
        else:
            print(token)

    def _jwt_secret(self) -> str:
        secret = self.bench.config.admin.jwt_secret
        if secret:
            return secret
        secret = secrets.token_urlsafe(32)
        toml_path = self.bench.path / "bench.toml"
        data = tomllib.loads(toml_path.read_text())
        data.setdefault("admin", {})["jwt_secret"] = secret
        write_toml(toml_path, data)
        self.bench.config.admin.jwt_secret = secret
        return secret
