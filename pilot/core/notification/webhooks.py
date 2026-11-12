from __future__ import annotations

import hashlib
import hmac
import json
import typing
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from pilot.internal.jsonl_log import utc_now

if typing.TYPE_CHECKING:
    from pilot.config.webhook import WebhookEndpoint
    from pilot.core.bench import Bench

PING_EVENT = "ping"
WEBHOOK_TIMEOUT_SECONDS = 10
_RESPONSE_EXCERPT_BYTES = 2000


@dataclass(frozen=True)
class WebhookDelivery:
    status: int
    body: str

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300


def get_webhook_body(bench: "Bench", payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """The fixed JSON every endpoint receives. `event`, `message` and `context` keep the older body's fields."""

    route = payload.get("action_route")
    return {
        "event": payload["event"],
        "delivery": uuid.uuid4().hex,
        "sent_at": utc_now(),
        "bench": bench.config.name,
        "title": payload.get("title") or payload["message"],
        "message": payload["message"],
        "severity": payload.get("severity", "Warning"),
        "category": payload.get("category"),
        "site": payload.get("site"),
        "url": f"{bench.config.admin_url}{route}" if route else None,
        "context": payload.get("context") or {},
    }


def send_webhook(endpoint: "WebhookEndpoint", body: dict[str, typing.Any]) -> WebhookDelivery:
    """POST one body. Any HTTP answer returns its status; a network failure raises OSError."""
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Pilot-Hookshot",
        "X-Pilot-Event": body["event"],
        "X-Pilot-Delivery": body["delivery"],
    }
    if endpoint.token:
        signature = hmac.new(endpoint.token.encode(), data, hashlib.sha256).hexdigest()
        headers["Authorization"] = f"Bearer {endpoint.token}"
        headers["X-Pilot-Signature-256"] = f"sha256={signature}"
    request = urllib.request.Request(endpoint.url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            return WebhookDelivery(response.status, _excerpt(response))
    except urllib.error.HTTPError as error:
        return WebhookDelivery(error.code, _excerpt(error))


def ping_webhook(bench: "Bench", endpoint: "WebhookEndpoint") -> WebhookDelivery:
    """Send a sample `ping` event to one endpoint, whatever events it chose."""
    payload = {
        "event": PING_EVENT,
        "title": "Test notification from Pilot",
        "message": f"{bench.config.name}: this endpoint receives Pilot notifications.",
        "severity": "Info",
        "action_route": "/notifications",
    }
    return send_webhook(endpoint, get_webhook_body(bench, payload))


def _excerpt(response) -> str:
    return response.read(_RESPONSE_EXCERPT_BYTES).decode(errors="replace")
