from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.managers.nginx_tls import live_cert_path, live_key_path, render_ssl_directives

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class NginxAdminConfigRenderer:
    def __init__(
        self,
        bench: "Bench",
        *,
        security_trio: str,
        acme_location: str,
        error_pages: str,
        xff_header: str,
    ) -> None:
        self.bench = bench
        self.security_trio = security_trio
        self.acme_location = acme_location
        self.error_pages = error_pages
        self.xff_header = xff_header

    def render(self, ssl_ready: bool = False, has_admin_cert: bool = False) -> str:
        admin = self.bench.config.admin
        nginx_config = self.bench.config.nginx
        proxy_block = (
            self.error_pages
            + self._render_open_cors_location("/api/v1/health")
            + self._render_open_cors_location("/api/v1/bootstrap")
            + self._render_admin_proxy_location()
        )

        # admin.tls off, or no cert yet: plain HTTP, never redirect to HTTPS.
        if not admin.tls or not ssl_ready or not has_admin_cert:
            return self._render_http_block(nginx_config.http_port, admin.domain, proxy_block)

        return self._render_https_redirect_block(
            nginx_config.http_port, admin.domain
        ) + self._render_https_block(
            nginx_config.https_port,
            admin.domain,
            live_cert_path(admin.domain),
            live_key_path(admin.domain),
            proxy_block,
        )

    def _render_http_block(self, http_port: int, domain: str, proxy_block: str) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + self.security_trio
            + self.acme_location
            + proxy_block
            + "}\n"
        )

    def _render_https_redirect_block(self, http_port: int, domain: str) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + self.security_trio
            + self.acme_location
            + "    location / {\n"
            "        return 301 https://$host$request_uri;\n"
            "    }\n"
            "}\n\n"
        )

    def _render_https_block(
        self,
        https_port: int,
        domain: str,
        cert,
        key,
        proxy_block: str,
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {domain};\n\n"
            + self.security_trio
            + render_ssl_directives(cert, key)
            + proxy_block
            + "}\n"
        )

    def _render_open_cors_location(self, path: str) -> str:
        """Probed cross-origin (e.g. ReconnectOverlay after a scheme change),
        so nginx answers with a wide-open CORS header regardless of the app."""
        return (
            f"    location = {path} {{\n"
            f"        proxy_pass         http://127.0.0.1:{self._admin_proxy_port()};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self.xff_header};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_hide_header  Access-Control-Allow-Origin;\n"
            f"        add_header         Access-Control-Allow-Origin * always;\n"
            f"    }}\n"
        )

    def _render_admin_proxy_location(self) -> str:
        return (
            f"    location / {{\n"
            f"        proxy_pass         http://127.0.0.1:{self._admin_proxy_port()};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self.xff_header};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"    }}\n"
        )

    def _admin_socket_activated(self) -> bool:
        return self.bench.config.production.process_manager == "systemd"

    def _admin_proxy_port(self) -> int:
        """Socket-activated gunicorn's internal port under systemd, else admin.port."""
        admin = self.bench.config.admin
        return admin.internal_port if self._admin_socket_activated() else admin.port
