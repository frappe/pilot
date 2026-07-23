from __future__ import annotations

import pwd
import re
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pilot.exceptions import CommandError
from pilot.internal.template import Template
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.nginx.waf_render import ModSecurityRenderer
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import (
    _privileged,
    default_nginx_config_dir,
    is_linux,
    service_command,
    service_running,
    which,
)
from pilot.managers.sudoers import has_passwordless_sudo_for, install_sudoers_grant, stage_and_copy
from pilot.managers.waf import WafManager
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.config import SiteConfig
    from pilot.core.bench import Bench

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_PILOT_INCLUDE = Path("/etc/nginx/conf.d/00-pilot.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

_SHARED_ERROR_DIR = Path("/usr/share/nginx/bench-error-pages")

_TEMPLATES = Path(__file__).parent / "templates"
_BENCH_TEMPLATE = Template.from_path(_TEMPLATES / "bench.conf.template")
_SERVER_TEMPLATE = Template.from_path(_TEMPLATES / "server.conf.template")
_ERROR_PAGE_TEMPLATE = Template.from_path(_TEMPLATES / "error_page.html.template")

_CORS_PATHS = ["/api/v1/health", "/api/v1/bootstrap"]

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

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def _shared_nginx_dir() -> Path:
    """Host-wide nginx artifacts the bench user owns, globbed in by 00-pilot.conf."""
    from pilot.utils import cli_root

    return cli_root() / "nginx"


def render_error_html(code: int, title: str, message: str) -> str:
    return _ERROR_PAGE_TEMPLATE.render(code=code, title=title, message=message)


def live_cert_path(domain: str) -> Path:
    return LETSENCRYPT_LIVE / domain / "fullchain.pem"


def live_key_path(domain: str) -> Path:
    return LETSENCRYPT_LIVE / domain / "privkey.pem"


def cert_files_exist(domain: str) -> bool:
    """Ensure a missing sudo grant does not take the whole bench to http."""
    # /etc/letsencrypt/live is root-only (0700), so stat with privilege.
    command = ["test", "-f", str(live_cert_path(domain)), "-a", "-f", str(live_key_path(domain))]
    try:
        run_command(_privileged(command))
    except CommandError:
        return False
    return True


class NginxConfigRenderer:
    """Turns a bench into nginx config text. All layout and branching lives in
    templates/*.conf.template; this class only prepares the data they render
    from. NginxManager owns writing and reloading what this produces."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._proxy_servers_cache: list[str] | None = None

    def generate_bench_config(self, sites: list[tuple["SiteConfig", bool]], admin_ssl: bool) -> str:
        """The whole per-bench config: upstream, every site vhost, admin vhost.
        Each site is paired with whether its HTTPS cert is ready to serve."""
        vhosts = [self._site_vhost(site, ssl) for site, ssl in sites]
        if self.bench.config.admin.domain:
            vhosts.append(self._admin_vhost(admin_ssl))
        return _BENCH_TEMPLATE.render(**self._bench_context(vhosts))

    def generate_server_config(self, error_dir: Path) -> str:
        """The host-wide catch-all vhost, shared by every bench on the box."""
        nginx = self.bench.config.nginx
        return _SERVER_TEMPLATE.render(
            http_port=nginx.http_port,
            https_port=nginx.https_port,
            error_dir=error_dir,
            error_codes=list(ERROR_PAGES),
        )

    @property
    def _proxy_servers(self) -> list[str]:
        """Edge-proxy IPs in front of this bench, if any; looked up once."""
        if self._proxy_servers_cache is None:
            from pilot.core.adapters.domain_provider import DomainRouteProvider

            self._proxy_servers_cache = DomainRouteProvider.proxy_servers()
        return self._proxy_servers_cache

    def _is_waf_active(self) -> bool:
        """Gated on the module + CRS actually being installed, so a vhost
        never references an absent module (which would fail nginx -t)."""
        return self.bench.config.waf.enabled and WafManager.is_installed()

    def _site_vhost(self, site: "SiteConfig", ssl: bool) -> SimpleNamespace:
        canonical = site.primary if (len(site.all_domains) > 1 and site.primary_domain) else ""
        return SimpleNamespace(
            kind="site",
            server_name=" ".join(site.all_domains),
            ssl=ssl,
            cert=live_cert_path(site.name),
            key=live_key_path(site.name),
            name=site.name,
            public_root=f"{self.bench.path}/sites/{site.name}/public",
            canonical=canonical,
        )

    def _admin_vhost(self, ssl: bool) -> SimpleNamespace:
        admin = self.bench.config.admin
        socket_activated = self.bench.config.production.process_manager == "systemd"
        return SimpleNamespace(
            kind="admin",
            server_name=admin.domain,
            ssl=ssl,
            cert=live_cert_path(admin.domain),
            key=live_key_path(admin.domain),
            port=admin.internal_port if socket_activated else admin.port,
        )

    def _bench_context(self, vhosts: list[SimpleNamespace]) -> dict[str, Any]:
        config = self.bench.config
        nginx = config.nginx
        return {
            "upstream_name": config.name,
            "upstream_server": GunicornManager(self.bench).upstream_server,
            "http_port": nginx.http_port,
            "https_port": nginx.https_port,
            "client_max_body_size": nginx.client_max_body_size,
            "socketio_port": config.socketio_port,
            "sites_root": f"{self.bench.path}/sites",
            "logs_path": str(self.bench.logs_path),
            "acme_root": config.letsencrypt.webroot_path,
            "error_dir": self.bench.config_path / "nginx" / "error_pages",
            "error_codes": list(ERROR_PAGES),
            "proxy_servers": self._proxy_servers,
            "proxy_peers": "|".join(re.escape(ip) for ip in self._proxy_servers),
            "firewall": config.firewall,
            "waf_active": self._is_waf_active(),
            "waf_rules_file": self.bench.config_path / "modsecurity" / "main.conf",
            "cors_paths": _CORS_PATHS,
            "vhosts": vhosts,
        }


class NginxManager:
    """Installs, configures, and reloads nginx for a bench, via NginxConfigRenderer."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._renderer = NginxConfigRenderer(bench)
        self._modsec = ModSecurityRenderer(bench)

    def is_installed(self) -> bool:
        return shutil.which("nginx") is not None

    def install(self) -> None:
        if not self.is_installed():
            get_package_manager().install("nginx")

    def setup_sudoers(self):
        """Give nginx passwordless sudo for exactly the commands reload needs.
        Idempotent: same deterministic content every call."""
        if self.has_passwordless_sudo:
            return
        bench_user = pwd.getpwuid(self.bench.path.stat().st_uid).pw_name
        systemctl = which("systemctl") or "/bin/systemctl"
        nginx = which("nginx") or "/usr/sbin/nginx"
        install_sudoers_grant(
            self.bench.config_path / "nginx",
            bench_user,
            "nginx",
            [
                f"{nginx} -t",
                f"{nginx} -T",
                f"{systemctl} start nginx",
                f"{systemctl} stop nginx",
                f"{systemctl} reload nginx",
            ],
        )

    @property
    def has_passwordless_sudo(self) -> bool:
        """True when the sudoers grant from `setup_sudoers` lets this user run
        nginx commands without a password prompt."""
        nginx = which("nginx") or "/usr/sbin/nginx"
        return has_passwordless_sudo_for([nginx, "-t"])

    def generate_config(self, ssl_ready: bool = False) -> None:
        nginx_dir = self.bench.config_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)
        self._write_error_pages(nginx_dir)
        self._write_waf_files()
        # admin.tls = False makes the whole bench HTTP-only: a central proxy
        # terminates TLS, so neither sites nor the admin serve HTTPS here.
        tls = self.bench.config.admin.tls
        sites = [
            (site.config, tls and ssl_ready and site.config.ssl and self.has_covering_cert(site.config))
            for site in self.bench.sites()
        ]
        admin_ssl = tls and ssl_ready and self.has_admin_cert
        (nginx_dir / "include.conf").write_text(self._renderer.generate_bench_config(sites, admin_ssl))

    def reload_for_site_change(self) -> None:
        if not self.bench.config.production.enabled or not self.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        self.generate_config(ssl_ready=True)
        self.reload()

    def _write_error_pages(self, nginx_dir: Path) -> None:
        error_dir = nginx_dir / "error_pages"
        error_dir.mkdir(parents=True, exist_ok=True)
        for code, (title, message) in ERROR_PAGES.items():
            (error_dir / f"{code}.html").write_text(render_error_html(code, title, message))

    def install_default_server(self) -> None:
        """Install the shared catch-all vhost and error pages. Idempotent."""
        if self.has_shared_include:
            error_dir = _shared_nginx_dir() / "error_pages"
            error_dir.mkdir(parents=True, exist_ok=True)
            for code, (title, message) in ERROR_PAGES.items():
                (error_dir / f"{code}.html").write_text(render_error_html(code, title, message))
            catchall = _shared_nginx_dir() / "00-bench-default.conf"
            catchall.write_text(self._renderer.generate_server_config(error_dir))
            return

        staging = self.bench.config_path / "nginx"
        for code, (title, message) in ERROR_PAGES.items():
            staged = staging / f"_catchall_{code}.html"
            staged.write_text(render_error_html(code, title, message))
            run_command(
                _privileged(
                    [
                        "install",
                        "-D",
                        "-m",
                        "644",
                        str(staged),
                        str(_SHARED_ERROR_DIR / f"{code}.html"),
                    ]
                )
            )
            staged.unlink()

        catchall_conf = default_nginx_config_dir() / "00-bench-default.conf"
        self._stage_and_copy(self._renderer.generate_server_config(_SHARED_ERROR_DIR), catchall_conf)

        # The distro's stock default site also claims default_server on :80;
        # nginx rejects a duplicate, so drop it and let ours win.
        default_site = Path("/etc/nginx/sites-enabled/default")
        if default_site.exists() or default_site.is_symlink():
            run_command(_privileged(["rm", "-f", str(default_site)]))

    def _write_waf_files(self) -> None:
        """Write this bench's ModSecurity rule files. No-op when the WAF is off;
        the CRS baseline itself is shared, installed by WafManager."""
        waf = self.bench.config.waf
        if not waf.enabled:
            return
        modsec_dir = self._modsec.modsec_dir()
        modsec_dir.mkdir(parents=True, exist_ok=True)
        (self.bench.path / "logs").mkdir(exist_ok=True)
        (modsec_dir / "modsecurity.conf").write_text(self._modsec.render_engine(waf))
        (modsec_dir / "overrides.conf").write_text(self._modsec.render_overrides(waf))
        (modsec_dir / "custom_rules.conf").write_text(self._modsec.render_custom_rules(waf))
        (modsec_dir / "exclusions.conf").write_text(self._modsec.render_exclusions(waf))
        (modsec_dir / "main.conf").write_text(self._modsec.render_main(modsec_dir))

    @property
    def config_dir(self) -> Path:
        # Path("") is truthy (no __bool__ override), so `or` alone never falls
        # back here - compare explicitly against the empty-config sentinel.
        configured = self.bench.config.nginx.config_dir
        return configured if configured != Path("") else default_nginx_config_dir()

    @property
    def include_path(self) -> Path:
        return self.bench.config_path / "nginx" / "include.conf"

    @property
    def has_shared_include(self) -> bool:
        """True when the installer's 00-pilot.conf already globs this bench in,
        so publishing a vhost needs no privileges at all."""
        try:
            directives = _PILOT_INCLUDE.read_text()
        except OSError:
            return False
        patterns = re.findall(r"^\s*include\s+([^;]+);", directives, re.MULTILINE)
        return any(fnmatch(str(self.include_path), pattern.strip()) for pattern in patterns)

    def install_config(self) -> None:
        nginx_dir = self.config_dir
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"

        # A symlink from before the shared include would load the same vhost
        # twice, so it goes either way.
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        if not self.has_shared_include:
            self._prune_dangling_symlinks(nginx_dir)
            run_command(_privileged(["ln", "-s", str(self.include_path), str(symlink_path)]))
        self._set_worker_user()
        if self.bench.config.waf.enabled:
            self._ensure_modsecurity_module()
        self.install_default_server()
        self._reload_or_rollback(symlink_path)

    def _stage_and_copy(self, content: str, target: Path, validate: list[str] | None = None) -> None:
        """Sudo-copy content into a root-owned target via a bench-owned staging file."""
        stage_and_copy(self.bench.config_path / "nginx", content, target, validate)

    def _ensure_modsecurity_module(self) -> None:
        """Debian auto-enables the module; elsewhere inject a load_module line.
        No-op when not installed - the reload's nginx -t catches that."""
        if self._module_already_loaded():
            return
        module_path = WafManager.module_path()
        if module_path is None:
            return
        original = _NGINX_CONF.read_text()
        self._stage_and_copy(f"load_module {module_path};\n" + original, _NGINX_CONF)

    @staticmethod
    def _module_already_loaded() -> bool:
        try:
            if "ngx_http_modsecurity_module" in _NGINX_CONF.read_text():
                return True
        except OSError:
            return True
        modules_dir = Path("/etc/nginx/modules-enabled")
        return modules_dir.is_dir() and any("modsecurity" in entry.name for entry in modules_dir.iterdir())

    @staticmethod
    def _prune_dangling_symlinks(nginx_dir: Path) -> None:
        """A bench dropped without its own teardown leaves a dangling symlink
        here, which fails nginx -t for every bench sharing this config dir."""
        if not nginx_dir.is_dir():
            return
        for entry in nginx_dir.iterdir():
            if entry.is_symlink() and not entry.exists():
                run_command(_privileged(["unlink", str(entry)]))

    def _reload_or_rollback(self, symlink_path: Path) -> None:
        """A bad config for this bench must not take nginx down for every
        other bench on the box - unpublish it and re-raise."""
        try:
            self.reload()
        except Exception:
            self._unpublish(symlink_path)
            raise

    def _unpublish(self, symlink_path: Path) -> None:
        """Take this bench's vhost out of nginx's view, however it got there."""
        if self.has_shared_include:
            self.include_path.rename(self.include_path.with_name("include.conf.broken"))
        elif symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))

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
        self._stage_and_copy(updated, _NGINX_CONF)

    def uninstall_config(self) -> None:
        """Take this bench's vhost out of nginx and reload. Certs are kept."""
        self._unpublish(self.config_dir / f"{self.bench.config.name}.conf")
        self.reload()

    def reload(self) -> None:
        run_command(_privileged(["nginx", "-t"]), timeout=10)
        if not is_linux():
            run_command(["nginx", "-s", "reload"])
            return
        # reload needs a running nginx; a fresh install may not be started yet.
        action = "reload" if service_running("nginx") else "start"
        run_command(service_command(action, "nginx"))

    def cert_path(self, site: "SiteConfig") -> Path:
        return live_cert_path(site.name)

    def has_cert(self, site: "SiteConfig") -> bool:
        return cert_files_exist(site.name)

    def has_covering_cert(self, site: "SiteConfig") -> bool:
        """Cert exists and its SAN list covers every public domain, if any -
        so a failed --expand can't serve a stale cert over HTTPS."""
        from pilot.managers.letsencrypt import has_domain_coverage, public_domains

        if not self.has_cert(site):
            return False
        public = public_domains(site)
        return has_domain_coverage(self.cert_path(site), public) if public else True

    @property
    def admin_cert_path(self) -> Path:
        return live_cert_path(self.bench.config.admin.domain)

    @property
    def has_admin_cert(self) -> bool:
        return cert_files_exist(self.bench.config.admin.domain)
