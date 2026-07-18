from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.config.site import SiteConfig
    from pilot.core.bench import Bench


class NginxSiteLocations:
    def __init__(self, bench: "Bench", xff_header: str) -> None:
        self.bench = bench
        self.xff_header = xff_header

    def render_assets(self) -> str:
        return (
            "    location /assets {\n"
            "        try_files $uri =404;\n"
            "        expires 1y;\n"
            '        add_header Cache-Control "public, immutable";\n'
            "    }\n\n"
        )

    def render_files(self, site: "SiteConfig") -> str:
        return (
            f"    location ~ ^/files/.*\\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {{\n"
            f"        root {self.bench.path}/sites/{site.name}/public;\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def render_socketio(self, socketio_port: int, site_name: str) -> str:
        # X-Frappe-Site-Name is the site's real directory name, not $host (a
        # custom domain), since Frappe resolves the site by this header.
        return (
            f"    location /socket.io {{\n"
            f"        proxy_pass         http://127.0.0.1:{socketio_port};\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header   Upgrade $http_upgrade;\n"
            f'        proxy_set_header   Connection "upgrade";\n'
            f"        proxy_set_header   X-Frappe-Site-Name {site_name};\n"
            f"        proxy_set_header   Origin $scheme://$http_host;\n"
            f"        proxy_set_header   Host $host;\n"
            f"    }}\n\n"
        )

    def render_proxy(self, bench_name: str, site: "SiteConfig") -> str:
        # Redirect non-primary hosts to the primary domain, only when one was
        # explicitly chosen - else site.primary falls back to the (possibly
        # internal) site name and would 301 public traffic to an unreachable host.
        redirect = ""
        if len(site.all_domains) > 1 and site.primary_domain:
            redirect = (
                f'        if ($host != "{site.primary}") {{\n'
                f"            return 301 $scheme://{site.primary}$request_uri;\n"
                f"        }}\n"
            )
        return (
            "    location / {\n"
            + redirect
            + f"        proxy_pass         http://bench-{bench_name};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self.xff_header};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_set_header   X-Frappe-Site-Name {site.name};\n"
            f"    }}\n"
        )
