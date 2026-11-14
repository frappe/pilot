from __future__ import annotations

import contextlib
import json
import typing
from datetime import UTC, datetime

from pilot.config.mail import MailConfig
from pilot.core.notification.mail import AlertMail
from pilot.integrations.central import CentralClient, CentralClientError

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


class Notifier:
    """Sends a bench's events to its webhooks, and alerts also to mail and Central.

    A payload holds `event`, `message` and `context`, and may add `title`, `severity`,
    `category`, `site` and `action_route`."""

    # Events that also go to mail and Central. Others, such as one failed backup attempt
    # or a finished task, go to webhooks and the feed only.
    alert_events = frozenset(
        {
            "resource_limit_breached",
            "site_down",
            "backups_failing",
            "certificate_expiry",
            "certificate_failed",
        }
    )
    # Central refuses an event type it does not define, so each alert uses Central's name.
    central_event_types: typing.ClassVar[dict[str, str]] = {
        "resource_limit_breached": "resource_limit_breached",
        "site_down": "site_unavailable",
        "backups_failing": "backup_failure",
    }

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def send(self, payload: dict[str, typing.Any]) -> bool:
        """Send one event to every sink that takes it; True when any of them accepted it."""
        delivered = self.send_to_webhooks(payload)
        if not self.is_alert(payload):
            return delivered
        delivered = self.send_to_mail(payload) or delivered
        return self.send_to_central(payload) or delivered

    def send_to_webhooks(self, payload: dict[str, typing.Any]) -> bool:
        """Post the event to each webhook endpoint that subscribed to it."""
        from pilot.core.notification.webhooks import get_webhook_body, send_webhook

        body = get_webhook_body(self.bench, payload)
        delivered = False
        for endpoint in self.bench.config.webhook_endpoints:
            if not endpoint.receives(body["event"]):
                continue
            with contextlib.suppress(OSError):
                delivered = send_webhook(endpoint, body).is_success or delivered
        return delivered

    def send_to_mail(self, payload: dict[str, typing.Any]) -> bool:
        """Email the alert to the bench's alert recipients, when mail is configured."""
        recipients = self.bench.config.resource_limits.email_recipients
        mail = MailConfig.read(self.bench.sites_path)
        if not mail.is_configured or not recipients:
            return False
        try:
            AlertMail(mail).send(recipients, payload)
        except OSError:
            return False
        return True

    def send_to_central(self, payload: dict[str, typing.Any]) -> bool:
        """Report the alert to Central, when the bench is managed and Central knows the event."""
        event_type = self.get_central_event_type(payload)
        if not self.bench.config.central.enabled or event_type is None:
            return False
        try:
            CentralClient().notify_central(
                event=event_type,
                message=payload["message"],
                context=payload.get("context"),
                reference_name=self.bench.config.name,
            )
        except CentralClientError as error:
            self.log_central_delivery(event_type, str(error))
            return False
        self.log_central_delivery(event_type)
        return True

    def log_central_delivery(self, event_type: str, error: str | None = None) -> None:
        """Append the result to notifications.json.log. The log shipper sends it to Datum, where Beacon alerts on failures."""
        accepted = "did not accept" if error else "accepted"
        line = {
            "time": datetime.now(UTC).isoformat(),
            "level": "error" if error else "info",
            "message": f"Central {accepted} the {event_type} event",
            "sink": "central",
            "event": event_type,
            "delivered": "0" if error else "1",
        }
        if error:
            line["error"] = error[:500]
        with contextlib.suppress(OSError), (self.bench.logs_path / "notifications.json.log").open("a") as log:
            log.write(json.dumps(line) + "\n")

    @classmethod
    def is_alert(cls, payload: dict[str, typing.Any]) -> bool:
        """Whether the event also goes to mail and Central."""
        return payload["event"] in cls.alert_events or cls.is_failed_update(payload)

    @staticmethod
    def is_failed_update(payload: dict[str, typing.Any]) -> bool:
        """Whether the event is a failed task in the Updates category."""
        return payload["event"] == "task_failed" and payload.get("category") == "Updates"

    @classmethod
    def get_central_event_type(cls, payload: dict[str, typing.Any]) -> str | None:
        """Central's event type for this payload, or None when Central has no matching type."""
        if cls.is_failed_update(payload):
            return "migration_failure"
        return cls.central_event_types.get(payload["event"])
