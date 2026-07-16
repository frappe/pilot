from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    make_response,
    redirect,
    request,
)

from admin.backend.api.responses import created_response, error_response
from admin.backend.auth import allow_unauthenticated, require_scope
from admin.backend.rate_limit import rate_limit
from admin.backend.site_paths import site_config_path
from admin.backend.site_login_handoff import SiteLoginHandoffStore
from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.utils import normalize_host

site_login_bp = Blueprint("site-login", __name__)
_HANDOFF_PATH = "/api/v1/site-login-handoffs"


@dataclass(frozen=True)
class SiteLoginTarget:
    host: str
    exchange_url: str
    redirect_url: str
    secure: bool


@site_login_bp.post("/sites/<name>/login-links")
@require_scope(lambda kwargs: kwargs["name"])
@rate_limit(10, 60, user_ip=True)
def create_login_link(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return error_response("site_not_found", "Site not found.", 404)
    try:
        site_config = json.loads(config_path.read_text())
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )
    if not isinstance(site_config, dict):
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )

    target = _login_target(config, name, site_config)
    issued = _handoffs().issue(
        name,
        target.redirect_url,
        host=target.host,
        secure=target.secure,
    )
    response = created_response(
        {
            "url": target.exchange_url,
            "method": "POST",
            "handoff_token": issued.token,
            "expires_at": datetime.fromtimestamp(
                issued.handoff.expires_at,
                tz=timezone.utc,
            ).isoformat(),
        },
        target.exchange_url,
    )
    return _no_store(response)


@site_login_bp.post("/site-login-handoffs")
@allow_unauthenticated
@rate_limit(10, 60, user_ip=True)
def consume_login_handoff():
    token = request.form.get("handoff_token", "")
    handoff = _handoffs().consume(token, _request_host())
    if handoff is None:
        return _no_store(
            error_response(
                "invalid_login_handoff",
                "The site login handoff is invalid or expired.",
                401,
            )
        )

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        sid = create_site_session(bench_root, handoff.site)
    except Exception:
        sid = None
    if not sid:
        return _no_store(
            error_response(
                "site_login_unavailable",
                "Could not create a site login session.",
                503,
            )
        )

    response = redirect(handoff.redirect_url, code=303)
    response.set_cookie(
        "sid",
        sid,
        httponly=True,
        secure=handoff.secure,
        samesite="Lax",
    )
    return _no_store(response)


def create_site_session(
    bench_root: Path,
    site: str,
) -> str | None:
    python = bench_root / "env" / "bin" / "python"
    cli_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cli_root)
    result = subprocess.run(
        [
            str(python),
            "-m",
            "frappe.utils.bench_helper",
            "frappe",
            "--site",
            site,
            "execute",
            "pilot.internal.site_session.create_administrator_session",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(bench_root / "sites"),
        env=env,
    )
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    sid = lines[-1] if lines else ""
    return sid if re.fullmatch(r"[A-Za-z0-9._-]+", sid) else None


def _login_target(
    config: BenchConfig,
    site: str,
    site_config: dict,
) -> SiteLoginTarget:
    host = _primary_host(site, site_config)
    if not config.production.enabled:
        return SiteLoginTarget(
            host=host,
            exchange_url=_origin("http", host, config.admin.port) + _HANDOFF_PATH,
            redirect_url=_origin("http", host, config.http_port) + "/desk",
            secure=False,
        )

    proxy_tls = (
        current_app.config["SESSION_COOKIE_SECURE"] and not config.admin.tls
    )
    secure = proxy_tls or (config.admin.tls and bool(site_config.get("ssl")))
    scheme = "https" if secure else "http"
    if proxy_tls:
        port = 443
    else:
        port = (
            config.nginx.https_port
            if secure
            else config.nginx.http_port
        )
    origin = _origin(scheme, host, port)
    return SiteLoginTarget(
        host=host,
        exchange_url=origin + _HANDOFF_PATH,
        redirect_url=origin + "/desk",
        secure=secure,
    )


def _primary_host(site: str, site_config: dict) -> str:
    candidates = {normalize_host(site)}
    for entry in site_config.get("domains") or []:
        domain = entry.get("domain") if isinstance(entry, dict) else entry
        if isinstance(domain, str):
            candidates.add(normalize_host(domain))
    host_name = site_config.get("host_name")
    if isinstance(host_name, str) and host_name.strip():
        parsed = urlsplit(
            host_name if "://" in host_name else f"//{host_name}"
        )
        primary = normalize_host(parsed.hostname or "")
        if primary in candidates:
            return primary
    return normalize_host(site)


def _origin(scheme: str, host: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{host}{suffix}"


def _request_host() -> str:
    return request.host.split(":", 1)[0]


def _handoffs() -> SiteLoginHandoffStore:
    return current_app.extensions["site_login_handoffs"]


def _no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
