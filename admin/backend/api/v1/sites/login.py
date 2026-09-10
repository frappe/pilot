from __future__ import annotations

from urllib.parse import urlsplit

from flask import make_response

from pilot.core.site.login import is_host_resolvable, origin, primary_host

__all__ = ["no_store", "origin", "primary_host", "unreachable_host_hint"]


def no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def unreachable_host_hint(url: str) -> str | None:
    """A UI hint when the URL's host does not resolve on this machine,
    so the browser most likely cannot reach it either."""
    host = urlsplit(url).hostname or ""
    if not host or is_host_resolvable(host):
        return None
    return (
        f"'{host}' does not resolve on this machine. Add it to /etc/hosts "
        "or use a *.localhost site name so the browser can reach the site."
    )
