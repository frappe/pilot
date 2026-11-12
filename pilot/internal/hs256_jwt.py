from __future__ import annotations

import base64
import hashlib
import hmac
import json


def _b64url(data: bytes) -> str:
    """Base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode(payload: dict, secret: str) -> str:
    """A JWT for the payload, signed with HMAC-SHA256."""
    segments = [
        _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()),
        _b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signature = hmac.new(secret.encode(), ".".join(segments).encode(), hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)
