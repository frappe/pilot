from __future__ import annotations

from pathlib import Path

_LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def live_cert_path(domain: str) -> Path:
    return _LETSENCRYPT_LIVE / domain / "fullchain.pem"


def live_key_path(domain: str) -> Path:
    return _LETSENCRYPT_LIVE / domain / "privkey.pem"
