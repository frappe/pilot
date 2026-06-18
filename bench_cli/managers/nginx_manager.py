from __future__ import annotations

import pwd
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.managers.gunicorn_manager import GunicornManager
from bench_cli.platform import get_package_manager, is_linux
from bench_cli.utils import run_command

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

if TYPE_CHECKING:
    from bench_cli.config.site_config import SiteConfig
    from bench_cli.core.bench import Bench


class NginxManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def is_installed(self) -> bool:
        return shutil.which("nginx") is not None

    def install(self) -> None:
        if not self.is_installed():
            get_package_manager().install("nginx")

    def generate_config(self, ssl_ready: bool = False) -> None:
        nginx_dir = self.bench.config_path / "nginx"
        sites_dir = nginx_dir / "sites"
        sites_dir.mkdir(parents=True, exist_ok=True)
        for site in self.bench.sites():
            site_ssl_ready = ssl_ready and self.cert_exists(site.config)
            conf_text = self._generate_site_config(site.config, site_ssl_ready)
            (sites_dir / f"{site.config.name}.conf").write_text(conf_text)
        # Expose the admin via nginx when it has its own domain, or — under
        # systemd, where it is socket-activated on an internal port — as a
        # domainless proxy listening on admin.port.
        if self.bench.config.admin.domain:
            conf_text = self._generate_admin_config(ssl_ready)
            (sites_dir / "_admin.conf").write_text(conf_text)
        elif self._admin_socket_activated():
            (sites_dir / "_admin.conf").write_text(self._generate_admin_domainless_config())
        self._write_include_conf(nginx_dir)

    def _admin_socket_activated(self) -> bool:
        return self.bench.config.production.process_manager == "systemd"

    def _admin_proxy_port(self) -> int:
        """Where the admin actually listens: the socket-activated gunicorn's
        internal port under systemd, else the Flask process on admin.port."""
        admin = self.bench.config.admin
        return admin.internal_port if self._admin_socket_activated() else admin.port

    def _write_include_conf(self, nginx_dir: Path) -> None:
        bench_name = self.bench.config.name
        include_path = nginx_dir / "include.conf"
        include_path.write_text(
            self._render_upstream_block(bench_name)
            + f"include {nginx_dir}/sites/*.conf;\n"
        )

    def _generate_site_config(self, site: "SiteConfig", ssl_ready: bool) -> str:
        bench_name = self.bench.config.name
        nginx_config = self.bench.config.nginx
        bench_root = self.bench.path

        if not site.ssl or not ssl_ready:
            return self._render_http_only_block(
                site, bench_name, nginx_config, bench_root
            )

        return (
            self._render_http_redirect_block(site, nginx_config)
            + self._render_https_block(site, bench_name, nginx_config, bench_root)
        )

    def _render_upstream_block(self, bench_name: str) -> str:
        upstream_server = GunicornManager(self.bench).upstream_server()
        return (
            f"upstream bench-{bench_name} {{\n"
            f"    server {upstream_server};\n"
            f"    keepalive 32;\n"
            f"}}\n\n"
        )

    def _render_http_only_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: object,
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        max_body = nginx_config.client_max_body_size
        http_port = nginx_config.http_port
        socketio_port = self.bench.config.socketio_port
        webroot = self.bench.config.letsencrypt.webroot_path

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port)
            + self._render_proxy_location(bench_name)
            + f"}}\n"
        )

    def _render_http_redirect_block(self, site: "SiteConfig", nginx_config: object) -> str:
        server_name = " ".join(site.all_domains)
        http_port = nginx_config.http_port
        webroot = self.bench.config.letsencrypt.webroot_path

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
            f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
        )

    def _render_https_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: object,
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        https_port = nginx_config.https_port
        max_body = nginx_config.client_max_body_size
        socketio_port = self.bench.config.socketio_port
        cert = self.cert_path(site)
        key = Path("/etc/letsencrypt/live") / site.name / "privkey.pem"

        ssl_directives = (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )

        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {server_name};\n\n"
            + ssl_directives
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port)
            + self._render_proxy_location(bench_name)
            + f"}}\n"
        )

    def _render_assets_location(self) -> str:
        return (
            f"    location /assets {{\n"
            f"        try_files $uri =404;\n"
            f"        expires 1y;\n"
            f'        add_header Cache-Control "public, immutable";\n'
            f"    }}\n\n"
        )

    def _render_files_location(self, site: "SiteConfig") -> str:
        return (
            f"    location ~ ^/files/.*\\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {{\n"
            f"        root {self.bench.path}/sites/{site.name}/public;\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_socketio_location(self, socketio_port: int) -> str:
        return (
            f"    location /socket.io {{\n"
            f"        proxy_pass         http://127.0.0.1:{socketio_port};\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header   Upgrade $http_upgrade;\n"
            f'        proxy_set_header   Connection "upgrade";\n'
            f"        proxy_set_header   X-Frappe-Site-Name $host;\n"
            f"        proxy_set_header   Origin $scheme://$http_host;\n"
            f"        proxy_set_header   Host $host;\n"
            f"    }}\n\n"
        )

    def _render_proxy_location(self, bench_name: str) -> str:
        return (
            f"    location / {{\n"
            f"        proxy_pass         http://bench-{bench_name};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_set_header   X-Frappe-Site-Name $host;\n"
            f"    }}\n"
        )

    def _generate_admin_config(self, ssl_ready: bool = False) -> str:
        admin = self.bench.config.admin
        nginx_config = self.bench.config.nginx
        webroot = self.bench.config.letsencrypt.webroot_path
        http_port = nginx_config.http_port
        https_port = nginx_config.https_port
        domain = admin.domain

        acme_block = (
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )
        proxy_block = self._render_admin_proxy_location()

        if not ssl_ready or not self.admin_cert_exists():
            return (
                f"server {{\n"
                f"    listen {http_port};\n"
                f"    listen [::]:{http_port};\n"
                f"    server_name {domain};\n\n"
                + acme_block
                + proxy_block
                + f"}}\n"
            )

        cert = self.admin_cert_path()
        key = Path("/etc/letsencrypt/live") / domain / "privkey.pem"
        ssl_directives = (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + acme_block
            + f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {domain};\n\n"
            + ssl_directives
            + proxy_block
            + f"}}\n"
        )

    def _render_admin_proxy_location(self) -> str:
        return (
            f"    location / {{\n"
            f"        proxy_pass         http://127.0.0.1:{self._admin_proxy_port()};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"    }}\n"
        )

    def _generate_admin_domainless_config(self) -> str:
        """Plain HTTP proxy on admin.port → socket-activated admin gunicorn.
        No domain or SSL: nginx just fronts the on-demand admin on its port."""
        return (
            f"server {{\n"
            f"    listen {self.bench.config.admin.port};\n"
            f"    listen [::]:{self.bench.config.admin.port};\n"
            f"    server_name _;\n\n"
            + self._render_admin_proxy_location()
            + f"}}\n"
        )

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    def admin_cert_exists(self) -> bool:
        domain = self.bench.config.admin.domain
        live_dir = Path("/etc/letsencrypt/live") / domain
        return (live_dir / "fullchain.pem").exists() and (live_dir / "privkey.pem").exists()

    def install_config(self) -> None:
        nginx_dir = self.bench.config.nginx.config_dir
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"
        source_path = self.bench.config_path / "nginx" / "include.conf"

        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(["sudo", "unlink", str(symlink_path)])
        run_command(["sudo", "ln", "-s", str(source_path), str(symlink_path)])
        self._set_worker_user()

    def _set_worker_user(self) -> None:
        """Run nginx workers as the bench owner. Idempotent."""
        owner = pwd.getpwuid(self.bench.path.stat().st_uid).pw_name
        directive = f"user {owner};"
        original = _NGINX_CONF.read_text()
        if _USER_DIRECTIVE.search(original):
            updated = _USER_DIRECTIVE.sub(directive, original, count=1)
        else:
            updated = directive + "\n" + original
        if updated == original:
            return
        staged = self.bench.config_path / "nginx" / "nginx.conf"
        staged.write_text(updated)
        run_command(["sudo", "cp", str(staged), str(_NGINX_CONF)])
        staged.unlink()

    def reload(self) -> None:
        run_command(["sudo", "nginx", "-t"])
        if is_linux():
            run_command(["sudo", "systemctl", "reload", "nginx"])
        else:
            run_command(["nginx", "-s", "reload"])

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def cert_exists(self, site: "SiteConfig") -> bool:
        live_dir = Path("/etc/letsencrypt/live") / site.name
        return (live_dir / "fullchain.pem").exists() and (live_dir / "privkey.pem").exists()
