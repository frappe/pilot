from __future__ import annotations

from pathlib import Path
from string import Template

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged - proxy_intercept_errors is off.
ERROR_PAGES = {
    403: ("Access blocked", "Your network doesn’t have access to this server."),
    404: ("Page not found", "The page you’re looking for doesn’t exist."),
    502: (
        "Temporarily unavailable",
        "The server isn’t responding right now. Please try again in a moment.",
    ),
    503: (
        "Service unavailable",
        "The service is temporarily unavailable. Please try again shortly.",
    ),
}


# $-placeholders (not .format) so the CSS braces below stay literal.
_ERROR_PAGE_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$code - $title</title>
<style>
:root{--bg:#fbfbfc;--fg:#1c2024;--muted:#6b7280;--accent:#d1d5db;--font:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);font-family:var(--font);-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
.box{text-align:center;padding:2.5rem 1.5rem;max-width:30rem}
.code{font-size:clamp(3.5rem,12vw,6rem);font-weight:700;line-height:1;letter-spacing:.05em;margin:0;color:var(--fg)}
.rule{width:2.5rem;height:3px;border-radius:999px;background:var(--accent);margin:1.5rem auto}
.title{font-size:1.125rem;font-weight:600;letter-spacing:-.01em;margin:0 0 .4rem}
.msg{font-size:.95rem;line-height:1.55;color:var(--muted);margin:0}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8eb;--muted:#9ba1a8;--accent:#2c2f36}}
</style>
</head>
<body>
<div class="box">
<p class="code">$code</p>
<div class="rule"></div>
<p class="title">$title</p>
<p class="msg">$message</p>
</div>
</body>
</html>
"""
)


def render_error_html(code: int, title: str, message: str) -> str:
    return _ERROR_PAGE_TEMPLATE.substitute(code=code, title=title, message=message)


class NginxErrorPages:
    def __init__(self, error_dir: Path) -> None:
        self.error_dir = error_dir

    def render_location(self) -> str:
        directives = self._directives()
        return (
            directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + "        allow all;\n"  # a firewall-denied client must still get its 403 page
            + f"        alias {self.error_dir}/;\n"
            + "    }\n\n"
        )

    @classmethod
    def render_catchall(cls, http_port: int, https_port: int, error_dir: Path) -> str:
        directives = cls(error_dir)._directives()
        return (
            # 256 fits any server_name; the stock 64-byte bucket overflows on
            # long custom/wildcard domains. Set once here, not per-bench.
            "server_names_hash_bucket_size 256;\n\n"
            "server {\n"
            f"    listen {http_port} default_server;\n"
            f"    listen [::]:{http_port} default_server;\n"
            "    server_name _;\n\n"
            + directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + f"        alias {error_dir}/;\n"
            + "    }\n\n"
            + "    location / {\n"
            + "        return 404;\n"
            + "    }\n"
            "}\n\n"
            # Without this, an https:// request for an http-only bench falls
            # through to the first 443 vhost and serves the wrong cert.
            "server {\n"
            f"    listen {https_port} ssl http2 default_server;\n"
            f"    listen [::]:{https_port} ssl http2 default_server;\n"
            "    server_name _;\n\n"
            "    ssl_reject_handshake on;\n"
            "}\n"
        )

    @staticmethod
    def _directives() -> str:
        return "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in ERROR_PAGES)
