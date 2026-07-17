from __future__ import annotations

import pwd
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.nginx_render import ERROR_PAGES, NginxConfigRenderer, render_error_html
from pilot.managers.packages import get_package_manager
from pilot.managers.waf import WafManager
from pilot.managers.platform import (
    _privileged,
    default_nginx_config_dir,
    is_linux,
    service_command,
    service_running,
)
from pilot.utils import run_command

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

_SHARED_ERROR_DIR = Path("/usr/share/nginx/bench-error-pages")


def _catchall_conf() -> Path:
    """Shared default_server vhost so unknown hosts get our 404, not nginx's."""
    return default_nginx_config_dir() / "00-bench-default.conf"


def _stock_default_sites() -> list[Path]:
    """The distro's own default vhost(s), which would conflict with our catch-all."""
    return [Path("/etc/nginx/sites-enabled/default")]


def _cert_files_exist(live_dir: Path) -> bool:
    # /etc/letsencrypt/live is root-only (0700), so stat with privilege
    # rather than letting Path.exists() raise EACCES for the bench user.
    import subprocess

    return subprocess.run(
        _privileged(["test", "-f", str(live_dir / "fullchain.pem"), "-a", "-f", str(live_dir / "privkey.pem")]),
        capture_output=True,
    ).returncode == 0


if TYPE_CHECKING:
    from pilot.config.site import SiteConfig
    from pilot.core.bench import Bench


class NginxManager:
    """Installs, configures, and reloads nginx for a bench, via NginxConfigRenderer."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._renderer = NginxConfigRenderer(bench)

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
        self._write_waf_files()
        # admin.tls = False makes the whole bench HTTP-only: a central proxy
        # terminates TLS, so neither sites nor the admin serve HTTPS here.
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            site_ssl_ready = tls and ssl_ready and self.cert_covers(site.config)
            conf_text = self._renderer.generate_site_config(site.config, site_ssl_ready)
            (sites_dir / f"{site.config.name}.conf").write_text(conf_text)
        # The admin is always reached via its (mandatory) domain in production;
        # nginx forwards that host to the local admin process.
        if self.bench.config.admin.domain:
            conf_text = self._renderer.generate_admin_config(ssl_ready, self.has_admin_cert)
            (sites_dir / "_admin.conf").write_text(conf_text)
        self._write_include_conf(nginx_dir)

    def reload_for_site_change(self) -> None:
        if not self.bench.config.production.enabled or not self.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        self.generate_config(ssl_ready=True)
        self.reload()

    def _write_include_conf(self, nginx_dir: Path) -> None:
        bench_name = self.bench.config.name
        include_path = nginx_dir / "include.conf"
        include_path.write_text(
            self._renderer._render_upstream_block(bench_name)
            + f"include {nginx_dir}/sites/*.conf;\n"
        )

    def _write_error_pages(self, nginx_dir: Path) -> None:
        error_dir = nginx_dir / "error_pages"
        error_dir.mkdir(parents=True, exist_ok=True)
        for code, (title, message) in ERROR_PAGES.items():
            (error_dir / f"{code}.html").write_text(render_error_html(code, title, message))

    def install_default_server(self) -> None:
        """Install the shared catch-all vhost and error pages. Idempotent."""
        staging = self.bench.config_path / "nginx"
        for code, (title, message) in ERROR_PAGES.items():
            staged = staging / f"_catchall_{code}.html"
            staged.write_text(render_error_html(code, title, message))
            run_command(_privileged(["install", "-D", "-m", "644", str(staged), str(_SHARED_ERROR_DIR / f"{code}.html")]))
            staged.unlink()

        staged = staging / "_catchall.conf"
        staged.write_text(
            self._renderer._render_catchall(
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

    def _write_waf_files(self) -> None:
        """Write this bench's ModSecurity rule files. No-op when the WAF is off;
        the CRS baseline itself is shared, installed by WafManager."""
        waf = self.bench.config.waf
        if not waf.enabled:
            return
        modsec_dir = self._renderer.modsec_dir()
        modsec_dir.mkdir(parents=True, exist_ok=True)
        (self.bench.path / "logs").mkdir(exist_ok=True)
        (modsec_dir / "modsecurity.conf").write_text(self._renderer._render_modsec_engine(waf))
        (modsec_dir / "overrides.conf").write_text(self._renderer._render_modsec_overrides(waf))
        (modsec_dir / "custom_rules.conf").write_text(self._renderer._render_modsec_custom_rules(waf))
        (modsec_dir / "exclusions.conf").write_text(self._renderer._render_modsec_exclusions(waf))
        (modsec_dir / "main.conf").write_text(self._renderer._render_modsec_main(modsec_dir))

    @property
    def config_dir(self) -> Path:
        # Path("") is truthy (no __bool__ override), so `or` alone never falls
        # back here - compare explicitly against the empty-config sentinel.
        configured = self.bench.config.nginx.config_dir
        return configured if configured != Path("") else default_nginx_config_dir()

    def install_config(self) -> None:
        nginx_dir = self.config_dir
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"
        source_path = self.bench.config_path / "nginx" / "include.conf"

        self._prune_dangling_symlinks(nginx_dir)
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        run_command(_privileged(["ln", "-s", str(source_path), str(symlink_path)]))
        self._set_worker_user()
        if self.bench.config.waf.enabled:
            self._ensure_modsecurity_module()
        self.install_default_server()
        self._reload_or_rollback(symlink_path)

    def _ensure_modsecurity_module(self) -> None:
        """Debian auto-enables the module; elsewhere inject a load_module line.
        No-op when not installed - the reload's nginx -t catches that."""
        if self._module_already_loaded():
            return
        module_path = WafManager.module_path()
        if module_path is None:
            return
        original = _NGINX_CONF.read_text()
        updated = f"load_module {module_path};\n" + original
        staged = self.bench.config_path / "nginx" / "nginx.conf"
        staged.write_text(updated)
        run_command(_privileged(["cp", str(staged), str(_NGINX_CONF)]))
        staged.unlink()

    @staticmethod
    def _module_already_loaded() -> bool:
        """A load_module line or modules-enabled drop-in, not just the .so on
        disk. Unreadable nginx.conf (unprivileged bench) counts as loaded -
        nginx -t is the authoritative check either way."""
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
        other bench on the box - undo the symlink and re-raise."""
        try:
            self.reload()
        except Exception:
            run_command(_privileged(["unlink", str(symlink_path)]))
            raise

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
        """Remove this bench's vhost symlink and reload. Certs are kept."""
        symlink_path = self.config_dir / f"{self.bench.config.name}.conf"
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
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
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def has_cert(self, site: "SiteConfig") -> bool:
        return _cert_files_exist(Path("/etc/letsencrypt/live") / site.name)

    def cert_covers(self, site: "SiteConfig") -> bool:
        """Cert exists and its SAN list covers every public domain, if any -
        so a failed --expand can't serve a stale cert over HTTPS."""
        from pilot.managers.letsencrypt import cert_covers, public_domains

        if not self.has_cert(site):
            return False
        public = public_domains(site)
        return cert_covers(self.cert_path(site), public) if public else True

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    @property
    def has_admin_cert(self) -> bool:
        return _cert_files_exist(Path("/etc/letsencrypt/live") / self.bench.config.admin.domain)
