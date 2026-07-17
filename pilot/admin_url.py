from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.config.bench import BenchConfig


def admin_url(config: "BenchConfig", dev_host: str = "localhost") -> str:
    """Resolve the URL the admin panel is reached at.

    Production: ``<scheme>://<admin.domain>`` where the scheme follows
    ``admin.tls`` (nginx fronts the domain). Development: there is no nginx, so
    the admin is reached directly on ``admin.port`` of the current host —
    callers pass the hostname the panel was opened through (the switcher) or
    fall back to ``localhost`` (the CLI)."""
    admin = config.admin
    if config.production.enabled:
        scheme = "https" if admin.tls else "http"
        return f"{scheme}://{admin.domain}"
    return f"http://{dev_host}:{admin.port}"
