from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pilot.core.site.config import read_site_config
from pilot.utils import normalize_host

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteLogin:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def admin_url(self, proxy_tls: bool = False) -> str | None:
        site_config = read_site_config(self.site.path)
        sid = self.create_session()
        if not sid:
            return None
        redirect_url = self.redirect_url(site_config, proxy_tls)
        return f"{redirect_url}{'&' if '?' in redirect_url else '?'}sid={sid}"

    def create_session(self) -> str | None:
        program = (
            "import sys, frappe\n"
            "from frappe.auth import CookieManager, LoginManager\n"
            "frappe.init(site=sys.argv[1], sites_path='.')\n"
            "frappe.connect()\n"
            "frappe.utils.set_request(path='/')\n"
            "frappe.local.cookie_manager = CookieManager()\n"
            "frappe.local.login_manager = LoginManager()\n"
            "frappe.local.login_manager.login_as('Administrator')\n"
            "frappe.db.commit()\n"
            "sys.stdout.write(frappe.session.sid)\n"
        )
        result = subprocess.run(
            [str(self.site.bench.python), "-c", program, self.site.config.name],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(self.site.bench.sites_path),
        )
        if result.returncode != 0:
            return None
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        sid = lines[-1] if lines else ""
        return sid if re.fullmatch(r"[A-Za-z0-9._-]+", sid) else None

    def redirect_url(self, site_config: dict, proxy_tls: bool = False) -> str:
        host = primary_host(self.site.config.name, site_config)
        config = self.site.bench.config
        if not config.production.enabled:
            return origin("http", host, config.http_port) + "/desk"

        secure = proxy_tls or (config.admin.tls and bool(site_config.get("ssl")))
        scheme = "https" if secure else "http"
        port = 443 if proxy_tls else (config.nginx.https_port if secure else config.nginx.http_port)
        return origin(scheme, host, port) + "/desk"


def primary_host(site: str, site_config: dict) -> str:
    candidates = {normalize_host(site)}
    for entry in site_config.get("domains") or []:
        domain = entry.get("domain") if isinstance(entry, dict) else entry
        if isinstance(domain, str):
            candidates.add(normalize_host(domain))
    host_name = site_config.get("host_name")
    if isinstance(host_name, str) and host_name.strip():
        parsed = urlsplit(host_name if "://" in host_name else f"//{host_name}")
        primary = normalize_host(parsed.hostname or "")
        if primary in candidates:
            return primary
    return normalize_host(site)


def origin(scheme: str, host: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{host}{suffix}"
