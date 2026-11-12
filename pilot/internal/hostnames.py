from __future__ import annotations

import contextlib


def normalize_host(host: str) -> str:
    """Canonical hostname for comparisons: lowercase, no trailing dot, IDNA."""
    if not host:
        return ""
    normalized = host.strip().lower().rstrip(".")
    with contextlib.suppress(UnicodeError, ValueError):
        normalized = normalized.encode("idna").decode("ascii")
    return normalized


def wildcard_suffix(pattern: str) -> str:
    """The fixed part of a wildcard pattern: "*.example.com" gives ".example.com"."""
    return pattern[1:] if pattern.startswith("*") else pattern


def matches_wildcard(domain: str, patterns: list[str]) -> bool:
    """Whether the domain is a subdomain covered by any of the wildcard patterns."""
    domain = normalize_host(domain)
    return any(
        domain != (suffix := wildcard_suffix(pattern)) and domain.endswith(suffix) for pattern in patterns
    )


def hosts_line_contains(line: str, hostname: str, address: str = "127.0.0.1") -> bool:
    """Whether an /etc/hosts line maps the hostname to the address."""
    tokens = line.split("#", 1)[0].split()
    return bool(tokens) and tokens[0] == address and hostname in tokens[1:]
