from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pilot.exceptions import BenchError

_TIMEOUT_SECONDS = 10


class CentralClientError(BenchError):
    """A Central call failed. `status_code` is Central's status when it answered
    and rejected; None when Central was unreachable. Callers need the two apart."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error_detail(body: bytes) -> str:
    """The reason out of a Frappe error response, so a rejection is not a bare status."""
    try:
        payload = json.loads(body.decode())
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""

    raw = payload.get("_server_messages")
    if raw:
        try:
            messages = [json.loads(item).get("message", "") for item in json.loads(raw)]
            if joined := ". ".join(m for m in messages if m):
                return joined
        except (ValueError, AttributeError, TypeError):
            pass

    # Frappe prefixes the exception class; the reason follows the colon.
    exception = payload.get("exception") or ""
    return exception.split(":", 1)[-1].strip() if ":" in exception else exception.strip()


class CentralClient:
    """Central transport using this host's pilot token."""

    TOKEN_HEADER = "X-Pilot-Token"

    def datum_token(self) -> dict[str, Any]:
        """The JWT to present to Datum, for metrics and logs alike, plus its TTL, resource
        id and this region's endpoint. Datum tells the two apart by route, not by token."""
        return self.forward("central.api.pilot.datum_token", "GET")

    def storage_regions(self) -> dict[str, str]:
        """Each region Frappe object storage serves now, mapped to its S3 endpoint."""
        return self.forward("central.api.pilot.storage_regions", "GET")

    def get_pilot_release(self, channel: str) -> dict[str, Any]:
        """The Pilot release Central lets this host update to: `{"tag": None}` when no
        rollout runs, else `{"tag": ..., "allowed": bool}`."""
        query = urllib.parse.urlencode({"channel": channel})
        return self.forward(f"central.api.pilot.pilot_release?{query}", "GET")

    def report_pilot_update(self, version: str, error: str | None = None) -> Any:
        """Tell Central which Pilot version this host now runs, or why its update failed."""
        return self.forward(
            "central.api.pilot.report_pilot_update", "POST", {"version": version, "error": error}
        )

    def notify_central(self, event: str, message: str, context: dict | None = None) -> Any:
        """Report a bench event to Central."""
        return self.forward(
            "central.notification.api.report_pilot_event",
            "POST",
            {"event": event, "message": message, "context": context or {}},
        )

    def forward(self, method_path: str, http_method: str, data: dict[str, Any] | None = None) -> Any:
        """Call a Central pilot-API method, returning its result with the
        ``{"message": ...}`` envelope unwrapped. The caller decides what's reachable."""
        endpoint, token = self._credentials()

        headers = {self.TOKEN_HEADER: token}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()

        url = f"{endpoint}/api/method/{method_path}"
        request = urllib.request.Request(url, data=body, method=http_method, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc.read())
            raise CentralClientError(
                detail or f"Central returned HTTP {exc.code} for {method_path}", status_code=exc.code
            ) from exc
        except OSError as exc:
            # URLError, and also a timeout or reset once connected, which urllib does not wrap.
            reason = getattr(exc, "reason", exc)
            raise CentralClientError(f"Cannot reach Central at {endpoint}: {reason}") from exc
        except ValueError as exc:
            raise CentralClientError(f"Central sent a non-JSON response for {method_path}: {exc}") from exc

        if isinstance(payload, dict) and "message" in payload:
            return payload["message"]
        return payload

    def _credentials(self) -> tuple[str, str]:
        """From instance metadata, which owns them. Nothing is persisted."""
        from pilot.integrations.central.metadata import InstanceMetadata

        credentials = InstanceMetadata().get_credentials()
        if credentials is None:
            raise CentralClientError("This host has no Central credential in its instance metadata.")

        return credentials["central_endpoint"].rstrip("/"), credentials["central_auth_token"]
