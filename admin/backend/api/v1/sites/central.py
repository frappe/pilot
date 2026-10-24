from __future__ import annotations

from typing import TypedDict

from flask import jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import malformed_body, site_name
from admin.backend.middleware import require_scope
from pilot.integrations.central import CentralClient, CentralClientError


class CentralAccountLink(TypedDict):
    url: str
    billing: str
    change_plan: str
    server: str


_ALLOWED_PREFIXES = ("central.billing.api.billing_api.",)
_ALLOWED_EXACT = frozenset(
    {
        "central.api.pilot.heartbeat",
        "central.api.pilot.get_team_identity_token",
        "central.api.email_oauth.get_active_providers",
        "central.api.email_oauth.start",
        "central.api.email_oauth.relay_token",
    }
)


def _is_allowed(method_path: str) -> bool:
    return method_path in _ALLOWED_EXACT or any(method_path.startswith(p) for p in _ALLOWED_PREFIXES)


@sites_bp.get("/<name>/central/<path:method_path>")
@sites_bp.post("/<name>/central/<path:method_path>")
@require_scope(site_name)
def central_proxy(name: str, method_path: str):
    if not _is_allowed(method_path):
        return error_response(
            "central_method_forbidden", f"Central method '{method_path}' is not permitted.", 403
        )
    data = request.get_json(silent=True) if request.method == "POST" else None
    if data is not None and not isinstance(data, dict):
        return malformed_body()
    try:
        return jsonify(CentralClient().forward(method_path, request.method, data))
    except CentralClientError as exc:
        # Central rejecting the input is not an outage; relaying a 502 would read
        # as though Central is down.
        if exc.status_code and 400 <= exc.status_code < 500:
            return error_response("central_rejected", str(exc), exc.status_code)
        return error_response("central_unreachable", str(exc), 502)


@sites_bp.get("/<name>/account-url")
@require_scope(site_name)
def account_url(name: str):
    """Central dashboard links for this site, safe for the site's browser to open.

    Central finds this server by its Pilot audience, as the admin dashboard's own links do."""
    from urllib.parse import urlencode

    from pilot.integrations.central import InstanceMetadata

    credentials = InstanceMetadata().get_credentials()
    if credentials is None:
        return error_response("central_not_configured", "Central is not configured.", 503)

    url = credentials["central_endpoint"].rstrip("/")
    audience = credentials["jwks_audience_id"]
    link: CentralAccountLink = {
        "url": url,
        "billing": f"{url}/dashboard/billing",
        "change_plan": f"{url}/dashboard/servers?{urlencode({'pilot': audience, 'action': 'resize'})}",
        "server": f"{url}/api/method/central.api.sso.open_bench?{urlencode({'pilot': audience})}",
    }
    return jsonify(link)
