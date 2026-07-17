from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from pilot.managers.gunicorn import GunicornManager
from pilot.managers.waf import SHARED_MODSEC_DIR, WafManager

if TYPE_CHECKING:
    from pilot.config.nginx import NginxConfig
    from pilot.config.site import SiteConfig
    from pilot.config.waf import WafCondition, WafConfig, WafRule
    from pilot.core.bench import Bench

# Custom-rule compilation maps (see _render_modsec_custom_rules). Field -> the
# ModSecurity variable it inspects; operator -> the ModSecurity operator, with the
# negated variants prefixed by "!"; action -> the SecLang disruptive action.
_WAF_FIELD_VARS = {
    "uri_path": "REQUEST_FILENAME",
    "uri_full": "REQUEST_URI",
    "query": "QUERY_STRING",
    "method": "REQUEST_METHOD",
    "source_ip": "REMOTE_ADDR",
    "user_agent": "REQUEST_HEADERS:User-Agent",
    "host": "REQUEST_HEADERS:Host",
}
_WAF_OPERATORS = {
    "is": "@streq",
    "is_not": "@streq",
    "contains": "@contains",
    "not_contains": "@contains",
    "starts_with": "@beginsWith",
    "matches": "@rx",
}
_WAF_NEGATED_OPERATORS = {"is_not", "not_contains"}
_WAF_ACTION_DIRECTIVES = {
    "block": "deny,status:403,log",
    "log": "pass,log,auditlog",
    "skip": "pass,ctl:ruleEngine=Off",
}

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged - proxy_intercept_errors is off.
ERROR_PAGES = {
    403: ("Access blocked", "Your network doesn’t have access to this server."),
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


class NginxConfigRenderer:
    """Builds nginx vhost/admin config text for a bench. No filesystem writes
    or service control - NginxManager owns installing what this renders."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._proxy_servers_cache: list[str] | None = None

    @property
    def _proxy_servers(self) -> list[str]:
        """Edge-proxy IPs in front of this bench, if any; looked up once."""
        if self._proxy_servers_cache is None:
            from pilot.core.domains import DomainRouteProvider

            self._proxy_servers_cache = DomainRouteProvider.proxy_servers()
        return self._proxy_servers_cache

    def _render_acme_location(self) -> str:
        """`allow all;` overrides any firewall deny so certbot can validate."""
        webroot = self.bench.config.letsencrypt.webroot_path
        return (
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        allow all;\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_proxy_trust(self) -> str:
        """Accept TCP only from trusted proxy IPs and read the real client IP
        from their X-Forwarded-For; empty (no restriction) when directly exposed.
        Tests $realip_remote_addr (the real TCP peer) so it runs before
        _render_firewall rewrites $remote_addr. Exempts the ACME path so a
        direct certbot hit still passes during setup."""
        proxies = self._proxy_servers
        if not proxies:
            return ""
        peers = "|".join(re.escape(ip) for ip in proxies)
        return (
            "".join(f"    set_real_ip_from   {ip};\n" for ip in proxies)
            + "    real_ip_header     X-Forwarded-For;\n"
            + "    real_ip_recursive  on;\n"
            + "    set $bench_from_proxy 0;\n"
            + f'    if ($realip_remote_addr ~ "^({peers})$") {{ set $bench_from_proxy 1; }}\n'
            + '    if ($request_uri ~ "^/\\.well-known/acme-challenge/") { set $bench_from_proxy 1; }\n'
            + "    if ($bench_from_proxy = 0) { return 403; }\n\n"
        )

    def _render_firewall(self) -> str:
        """First-match-wins allow/deny list. Trusted proxies are allowed first
        so a request without X-Forwarded-For (proxy IP in $remote_addr) can't
        be rejected by the configured rules. Empty when the firewall is off."""
        firewall = self.bench.config.firewall
        if not firewall.enabled:
            return ""
        lines = [f"    allow {ip};\n" for ip in self._proxy_servers]
        lines += [f"    {rule.action} {rule.ip};\n" for rule in firewall.rules]
        if firewall.default == "deny":
            lines.append("    deny all;\n")
        return "".join(lines) + "\n" if lines else ""

    def _waf_active(self) -> bool:
        """Gated on the module + CRS actually being installed, so a vhost
        never references an absent module (which would fail nginx -t)."""
        return self.bench.config.waf.enabled and WafManager.is_installed()

    def _render_waf(self) -> str:
        """Points nginx at this bench's generated ModSecurity rules file."""
        if not self._waf_active():
            return ""
        rules_file = self.modsec_dir() / "main.conf"
        return f"    modsecurity on;\n    modsecurity_rules_file {rules_file};\n\n"

    def _render_security_trio(self) -> str:
        return self._render_proxy_trust() + self._render_firewall() + self._render_waf()

    @staticmethod
    def _render_ssl_directives(cert: Path, key: Path) -> str:
        return (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )

    def modsec_dir(self) -> Path:
        return self.bench.config_path / "modsecurity"

    def _render_modsec_main(self, modsec_dir: Path) -> str:
        """Chain order CRS requires: engine, CRS baseline, overrides, custom
        rules (before CRS, so a block/skip wins, like Cloudflare), CRS rules,
        exclusions."""
        return (
            f"Include {modsec_dir}/modsecurity.conf\n"
            f"Include {SHARED_MODSEC_DIR}/crs-setup.conf\n"
            f"Include {modsec_dir}/overrides.conf\n"
            f"Include {modsec_dir}/custom_rules.conf\n"
            f"Include {SHARED_MODSEC_DIR}/rules/*.conf\n"
            f"Include {modsec_dir}/exclusions.conf\n"
        )

    def _render_modsec_engine(self, waf: "WafConfig") -> str:
        from pilot.config.waf import parse_nginx_size

        audit_log = self.bench.path / "logs" / "modsec_audit.log"
        # DetectionOnly must never block, so oversized bodies are inspected in part
        # rather than rejected; only full enforcement rejects them.
        body_action = "Reject" if waf.mode == "On" else "ProcessPartial"
        response_access = "On" if waf.inspect_responses else "Off"
        return (
            f"SecRuleEngine {waf.mode}\n"
            "SecRequestBodyAccess On\n"
            f"SecRequestBodyLimit {parse_nginx_size(waf.body_limit)}\n"
            "SecRequestBodyNoFilesLimit 131072\n"
            f"SecRequestBodyLimitAction {body_action}\n"
            "SecRequestBodyJsonDepthLimit 512\n"
            f"SecResponseBodyAccess {response_access}\n"
            "SecResponseBodyMimeType text/plain text/html text/xml application/json\n"
            "SecResponseBodyLimit 524288\n"
            "SecAuditEngine RelevantOnly\n"
            "SecAuditLogFormat JSON\n"
            "SecAuditLogType Serial\n"
            f"SecAuditLog {audit_log}\n"
            "SecAuditLogParts ABIJDEFHZ\n"
            "SecTmpDir /tmp\n"
            "SecDataDir /tmp\n"
            'SecDefaultAction "phase:1,pass,log"\n'
            'SecDefaultAction "phase:2,pass,log"\n'
        )

    def _render_modsec_overrides(self, waf: "WafConfig") -> str:
        """Per-bench CRS tuning, applied after crs-setup.conf so it wins.
        Rule ids start at 1000 to stay clear of CRS's reserved range."""
        lines = [
            f'SecAction "id:1000,phase:1,pass,nolog,'
            f"setvar:tx.blocking_paranoia_level={waf.paranoia},"
            f"setvar:tx.detection_paranoia_level={waf.paranoia},"
            f'setvar:tx.paranoia_level={waf.paranoia}"',
            f'SecAction "id:1001,phase:1,pass,nolog,'
            f'setvar:tx.inbound_anomaly_score_threshold={waf.inbound_threshold}"',
        ]
        for index, path in enumerate(waf.exempt_paths):
            lines.append(
                f'SecRule REQUEST_URI "@beginsWith {path}" '
                f'"id:{10000 + index},phase:1,pass,nolog,ctl:ruleEngine=Off"'
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_modsec_exclusions(waf: "WafConfig") -> str:
        """User SecLang lines (SecRuleRemoveById etc.), one per line."""
        return "\n".join(waf.exclusions) + ("\n" if waf.exclusions else "")

    @classmethod
    def _render_modsec_custom_rules(cls, waf: "WafConfig") -> str:
        """Compile custom rules to SecRules, phase 1 so a match short-circuits
        the CRS rules that follow. "all" chains conditions (AND); "any" emits
        one rule per condition (OR). Ids start at 100000, clear of CRS."""
        blocks = []
        for index, rule in enumerate(waf.custom_rules):
            if not rule.enabled or not rule.conditions:
                continue
            base_id = 100000 + index * 100
            action = _WAF_ACTION_DIRECTIVES[rule.action]
            msg = f"msg:'Custom rule: {rule.name or 'unnamed'}'"
            if rule.match == "any":
                blocks.append(cls._render_any_rule(rule, base_id, action, msg))
            else:
                blocks.append(cls._render_all_rule(rule, base_id, action, msg))
        return "\n".join(blocks) + ("\n" if blocks else "")

    @staticmethod
    def _condition_var_op(cond: "WafCondition") -> tuple[str, str]:
        """Return (variable, operator-argument) for one condition, e.g.
        ("REQUEST_METHOD", "!@streq GET")."""
        if cond.field == "header":
            variable = f"REQUEST_HEADERS:{cond.header_name}"
        else:
            variable = _WAF_FIELD_VARS[cond.field]
        negate = "!" if cond.operator in _WAF_NEGATED_OPERATORS else ""
        if cond.field == "source_ip" and cond.operator in ("is", "is_not"):
            operator = "@ipMatch"
            value = ",".join(entry.strip() for entry in cond.value.split(","))
        else:
            operator = _WAF_OPERATORS[cond.operator]
            value = cond.value
        return variable, f"{negate}{operator} {value}"

    @classmethod
    def _render_all_rule(cls, rule: "WafRule", base_id: int, action: str, msg: str) -> str:
        """AND: a single chained rule. The disruptive action sits on the starter and
        fires only when every chained condition also matches."""
        conditions = rule.conditions
        lines = []
        for position, cond in enumerate(conditions):
            variable, operator_arg = cls._condition_var_op(cond)
            last = position == len(conditions) - 1
            if position == 0:
                actions = [f"id:{base_id}", "phase:1", action, msg]
                if not last:
                    actions.append("chain")
                lines.append(f'SecRule {variable} "{operator_arg}" "{",".join(actions)}"')
            else:
                tail = ' "chain"' if not last else ""
                lines.append(f'    SecRule {variable} "{operator_arg}"{tail}')
        return "\n".join(lines)

    @classmethod
    def _render_any_rule(cls, rule: "WafRule", base_id: int, action: str, msg: str) -> str:
        """OR: one standalone rule per condition, each carrying the action."""
        lines = []
        for position, cond in enumerate(rule.conditions):
            variable, operator_arg = cls._condition_var_op(cond)
            actions = ",".join([f"id:{base_id + position}", "phase:1", action, msg])
            lines.append(f'SecRule {variable} "{operator_arg}" "{actions}"')
        return "\n".join(lines)

    def _xff_header(self) -> str:
        """Behind a trusted proxy, pass its X-Forwarded-For through unchanged
        rather than appending our own connecting address to it."""
        return "$http_x_forwarded_for" if self._proxy_servers else "$proxy_add_x_forwarded_for"

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    def generate_site_config(self, site: "SiteConfig", ssl_ready: bool) -> str:
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

    def error_pages_dir(self) -> Path:
        return self.bench.config_path / "nginx" / "error_pages"

    def _render_catchall(self, http_port: int, https_port: int, error_dir: Path) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in ERROR_PAGES)
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

    def _render_error_pages(self) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in ERROR_PAGES)
        return (
            directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + "        allow all;\n"  # a firewall-denied client must still get its 403 page
            + f"        alias {self.error_pages_dir()}/;\n"
            + "    }\n\n"
        )

    def _render_upstream_block(self, bench_name: str) -> str:
        upstream_server = GunicornManager(self.bench).upstream_server
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
        nginx_config: "NginxConfig",
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        max_body = nginx_config.client_max_body_size
        http_port = nginx_config.http_port
        socketio_port = self.bench.config.socketio_port

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            + self._render_security_trio()
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_acme_location()
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + "}\n"
        )

    def _render_http_redirect_block(self, site: "SiteConfig", nginx_config: "NginxConfig") -> str:
        server_name = " ".join(site.all_domains)
        http_port = nginx_config.http_port

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            + self._render_security_trio()
            + self._render_acme_location()
            + "    location / {\n"
            "        return 301 https://$host$request_uri;\n"
            "    }\n"
            "}\n\n"
        )

    def _render_https_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: "NginxConfig",
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        https_port = nginx_config.https_port
        max_body = nginx_config.client_max_body_size
        socketio_port = self.bench.config.socketio_port
        cert = self.cert_path(site)
        key = Path("/etc/letsencrypt/live") / site.name / "privkey.pem"

        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {server_name};\n\n"
            + self._render_security_trio()
            + self._render_ssl_directives(cert, key)
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + "}\n"
        )

    def _render_assets_location(self) -> str:
        return (
            "    location /assets {\n"
            "        try_files $uri =404;\n"
            "        expires 1y;\n"
            '        add_header Cache-Control "public, immutable";\n'
            "    }\n\n"
        )

    def _render_files_location(self, site: "SiteConfig") -> str:
        return (
            f"    location ~ ^/files/.*\\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {{\n"
            f"        root {self.bench.path}/sites/{site.name}/public;\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_socketio_location(self, socketio_port: int, site_name: str) -> str:
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

    def _render_proxy_location(self, bench_name: str, site: "SiteConfig") -> str:
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
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_set_header   X-Frappe-Site-Name {site.name};\n"
            f"    }}\n"
        )

    def generate_admin_config(self, ssl_ready: bool = False, has_admin_cert: bool = False) -> str:
        admin = self.bench.config.admin
        nginx_config = self.bench.config.nginx
        http_port = nginx_config.http_port
        https_port = nginx_config.https_port
        domain = admin.domain

        acme_block = self._render_acme_location()
        security_trio = self._render_security_trio()
        proxy_block = (
            self._render_error_pages()
            + self._render_open_cors_location("/api/v1/health")
            + self._render_open_cors_location("/api/v1/bootstrap")
            + self._render_admin_proxy_location()
        )

        # admin.tls off, or no cert yet: plain HTTP, never redirect to HTTPS.
        if not admin.tls or not ssl_ready or not has_admin_cert:
            return self._render_admin_http_block(http_port, domain, security_trio, acme_block, proxy_block)

        cert = self.admin_cert_path()
        key = Path("/etc/letsencrypt/live") / domain / "privkey.pem"
        return self._render_admin_https_redirect_block(
            http_port, domain, security_trio, acme_block
        ) + self._render_admin_https_block(https_port, domain, security_trio, cert, key, proxy_block)

    def _render_admin_http_block(
        self, http_port: int, domain: str, security_trio: str, acme_block: str, proxy_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + acme_block
            + proxy_block
            + "}\n"
        )

    def _render_admin_https_redirect_block(
        self, http_port: int, domain: str, security_trio: str, acme_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + acme_block
            + f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
        )

    def _render_admin_https_block(
        self, https_port: int, domain: str, security_trio: str, cert: Path, key: Path, proxy_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + self._render_ssl_directives(cert, key)
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
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
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
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"    }}\n"
        )

    def _admin_socket_activated(self) -> bool:
        return self.bench.config.production.process_manager == "systemd"

    def _admin_proxy_port(self) -> int:
        """Socket-activated gunicorn's internal port under systemd, else admin.port."""
        admin = self.bench.config.admin
        return admin.internal_port if self._admin_socket_activated() else admin.port
