from __future__ import annotations

from flask import make_response

from pilot.core.site.login import origin, primary_host

__all__ = ["no_store", "origin", "primary_host"]


def no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
