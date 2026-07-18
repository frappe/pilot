from __future__ import annotations

from pathlib import Path

_LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def live_cert_path(domain: str) -> Path:
    return _LETSENCRYPT_LIVE / domain / "fullchain.pem"


def live_key_path(domain: str) -> Path:
    return _LETSENCRYPT_LIVE / domain / "privkey.pem"


def render_ssl_directives(cert: Path, key: Path) -> str:
    return (
        f"    ssl_certificate     {cert};\n"
        f"    ssl_certificate_key {key};\n"
        f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
        f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
        f"    ssl_prefer_server_ciphers off;\n"
        f"    ssl_session_cache   shared:SSL:10m;\n"
        f"    ssl_session_timeout 1d;\n\n"
    )
