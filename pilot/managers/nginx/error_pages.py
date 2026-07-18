from __future__ import annotations

from pathlib import Path

from pilot.internal.template import Template

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged - proxy_intercept_errors is off.
ERROR_PAGES = {
    403: ("Access blocked", "Your network doesn't have access to this server."),
    404: ("Page not found", "The page you're looking for doesn't exist."),
    502: (
        "Temporarily unavailable",
        "The server isn't responding right now. Please try again in a moment.",
    ),
    503: (
        "Service unavailable",
        "The service is temporarily unavailable. Please try again shortly.",
    ),
}

_ERROR_PAGE_TEMPLATE = Template.from_path(Path(__file__).parent / "templates" / "error_page.html.template")


def render_error_html(code: int, title: str, message: str) -> str:
    return _ERROR_PAGE_TEMPLATE.render(code=code, title=title, message=message)
