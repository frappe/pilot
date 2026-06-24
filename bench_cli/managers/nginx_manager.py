from __future__ import annotations

import pwd
import re
import shutil
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from bench_cli.managers.gunicorn_manager import GunicornManager
from bench_cli.platform import (
    _privileged,
    default_nginx_config_dir,
    get_package_manager,
    is_alpine,
    is_linux,
    service_command,
    service_enable_command,
    service_running,
)
from bench_cli.utils import run_command

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

_SHARED_ERROR_DIR = Path("/usr/share/nginx/bench-error-pages")


def _catchall_conf() -> Path:
    """Server-wide catch-all so unknown hosts get our 404 instead of nginx's
    stock welcome page. One default_server per port, shared by every bench, so
    it lives in the distro's include dir (conf.d on Debian, http.d on Alpine)."""
    return default_nginx_config_dir() / "00-bench-default.conf"


def _stock_default_sites() -> list[Path]:
    """The distro's own default vhost(s), which also claim default_server on :80
    and would conflict with our catch-all. Debian symlinks sites-enabled/default
    (removed on every Linux, as upstream does); Alpine additionally ships
    http.d/default.conf."""
    sites = [Path("/etc/nginx/sites-enabled/default")]
    if is_alpine():
        sites.append(default_nginx_config_dir() / "default.conf")
    return sites

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged — proxy_intercept_errors is off.
_ERROR_PAGES = {
    404: ("Page not found", "The page you’re looking for doesn’t exist."),
    502: ("Temporarily unavailable", "The server isn’t responding right now. Please try again in a moment."),
    503: ("Service unavailable", "The service is temporarily unavailable. Please try again shortly."),
}


# $-placeholders (not .format) so the CSS braces below stay literal.
_ERROR_PAGE_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$code — $title</title>
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


def _render_error_html(code: int, title: str, message: str) -> str:
    return _ERROR_PAGE_TEMPLATE.substitute(code=code, title=title, message=message)

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
        self._write_error_pages(nginx_dir)
        # admin.tls = False makes the whole bench HTTP-only: a central proxy
        # terminates TLS, so neither sites nor the admin serve HTTPS here.
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            site_ssl_ready = tls and ssl_ready and self.cert_exists(site.config)
            conf_text = self._generate_site_config(site.config, site_ssl_ready)
            (sites_dir / f"{site.config.name}.conf").write_text(conf_text)
        # The admin is always reached via its (mandatory) domain in production;
        # nginx forwards that host to the local admin process.
        if self.bench.config.admin.domain:
            conf_text = self._generate_admin_config(ssl_ready)
            (sites_dir / "_admin.conf").write_text(conf_text)
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

    def _error_pages_dir(self) -> Path:
        return self.bench.config_path / "nginx" / "error_pages"

    def _write_error_pages(self, nginx_dir: Path) -> None:
        error_dir = nginx_dir / "error_pages"
        error_dir.mkdir(parents=True, exist_ok=True)
        for code, (title, message) in _ERROR_PAGES.items():
            (error_dir / f"{code}.html").write_text(_render_error_html(code, title, message))

    def install_default_server(self) -> None:
        """Install the server-wide catch-all vhost (and its error pages) so
        requests for unknown hosts return our 404, not nginx's welcome page.
        Idempotent; shared by all benches."""
        staging = self.bench.config_path / "nginx"
        for code, (title, message) in _ERROR_PAGES.items():
            staged = staging / f"_catchall_{code}.html"
            staged.write_text(_render_error_html(code, title, message))
            run_command(_privileged(["install", "-D", "-m", "644", str(staged), str(_SHARED_ERROR_DIR / f"{code}.html")]))
            staged.unlink()

        staged = staging / "_catchall.conf"
        staged.write_text(
            self._render_catchall(
                self.bench.config.nginx.http_port,
                self.bench.config.nginx.https_port,
                _SHARED_ERROR_DIR,
            )
        )
        run_command(_privileged(["cp", str(staged), str(_catchall_conf())]))
        staged.unlink()

        # The distro's stock default site also claims default_server on :80;
        # nginx rejects a duplicate, so drop it and let ours win.
        for default_site in _stock_default_sites():
            if default_site.exists() or default_site.is_symlink():
                run_command(_privileged(["rm", "-f", str(default_site)]))

    def _render_catchall(self, http_port: int, https_port: int, error_dir: Path) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in _ERROR_PAGES)
        return (
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
            # Without a default_server on the TLS port, an HTTPS request for a
            # host with no matching server block (e.g. an http-only bench reached
            # over https) silently falls through to the first 443 vhost defined,
            # serving the wrong bench's cert and content. ssl_reject_handshake
            # drops such handshakes outright (needs no certificate).
            "server {\n"
            f"    listen {https_port} ssl http2 default_server;\n"
            f"    listen [::]:{https_port} ssl http2 default_server;\n"
            "    server_name _;\n\n"
            "    ssl_reject_handshake on;\n"
            "}\n"
        )

    def _render_error_pages(self) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in _ERROR_PAGES)
        return (
            directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + f"        alias {self._error_pages_dir()}/;\n"
            + "    }\n\n"
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
            + self._render_error_pages()
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
            + self._render_error_pages()
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
        proxy_block = self._render_error_pages() + self._render_admin_proxy_location()

        # admin.tls = False: a central proxy terminates TLS, so nginx serves the
        # admin over plain HTTP on :80 and never redirects to HTTPS, even if a
        # stale cert is still on disk.
        if not admin.tls:
            return (
                f"server {{\n"
                f"    listen {http_port};\n"
                f"    listen [::]:{http_port};\n"
                f"    server_name {domain};\n\n"
                + acme_block
                + proxy_block
                + f"}}\n"
            )

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

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    def admin_cert_exists(self) -> bool:
        return self._cert_files_exist(Path("/etc/letsencrypt/live") / self.bench.config.admin.domain)

    def install_config(self) -> None:
        nginx_dir = self.bench.config.nginx.config_dir
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"
        source_path = self.bench.config_path / "nginx" / "include.conf"

        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        run_command(_privileged(["ln", "-s", str(source_path), str(symlink_path)]))
        self._set_worker_user()
        self.install_default_server()

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
        run_command(_privileged(["cp", str(staged), str(_NGINX_CONF)]))
        staged.unlink()

    def uninstall_config(self) -> None:
        """Remove this bench's nginx vhosts (the symlink in the config dir), then
        validate and reload the remaining machine-wide config. Certs are kept."""
        symlink_path = self.bench.config.nginx.config_dir / f"{self.bench.config.name}.conf"
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        self.reload()

    def reload(self) -> None:
        run_command(_privileged(["nginx", "-t"]))
        if not is_linux():
            run_command(["nginx", "-s", "reload"])
            return
        if is_alpine():
            # Alpine doesn't auto-start nginx after install — enable it, then
            # bring it up the first time and reload in place on later runs.
            run_command(service_enable_command("nginx"))
            action = "reload" if service_running("nginx") else "start"
            run_command(service_command(action, "nginx"))
            return
        run_command(service_command("reload", "nginx"))

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def cert_exists(self, site: "SiteConfig") -> bool:
        return self._cert_files_exist(Path("/etc/letsencrypt/live") / site.name)

    @staticmethod
    def _cert_files_exist(live_dir: Path) -> bool:
        # /etc/letsencrypt/live is root-only (0700), so stat with privilege
        # rather than letting Path.exists() raise EACCES for the bench user.
        import subprocess

        return subprocess.run(
            _privileged(["test", "-f", str(live_dir / "fullchain.pem"), "-a", "-f", str(live_dir / "privkey.pem")]),
            capture_output=True,
        ).returncode == 0
