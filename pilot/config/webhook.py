from dataclasses import dataclass, field

WEBHOOK_EVENTS = {
    "task_failed": "Task failed",
    "site_created": "Site created",
    "site_deleted": "Site deleted",
    "site_renamed": "Site renamed",
    "site_restored": "Site restored",
    "site_reinstalled": "Site reinstalled",
    "site_down": "Site down",
    "site_up": "Site up again",
    "backup_completed": "Backup completed",
    "backup_deleted": "Backup deleted",
    "backups_failing": "Backups failing",
    "app_installed": "App installed",
    "app_uninstalled": "App uninstalled",
    "app_added": "App added to the bench",
    "app_removed": "App removed from the bench",
    "app_branch_switched": "App branch switched",
    "update_completed": "Update finished",
    "update_restored": "Update restored",
    "update_dismissed": "Update dismissed",
    "pilot_updated": "Pilot updated",
    "certificate_issued": "Certificate issued",
    "certificate_expiry": "Certificate expiring or expired",
    "certificate_failed": "Certificate failed to issue or renew",
    "resource_limit_breached": "Resource limit crossed",
    "resource_limit_recovered": "Resource use back under the limit",
}


@dataclass
class WebhookEndpoint:
    """One webhook receiver. An empty `events` list receives every event; an empty token sends no auth."""

    url: str
    token: str = ""
    events: list[str] = field(default_factory=list)

    def receives(self, event: str) -> bool:
        """Whether the endpoint receives the event."""
        return not self.events or event in self.events

    def validate(self) -> None:
        """Raise when the endpoint is invalid."""
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"Webhook URL must start with http:// or https://: {self.url!r}.")
        unknown = set(self.events) - WEBHOOK_EVENTS.keys()
        if unknown:
            raise ValueError(f"Unknown webhook events for {self.url}: {', '.join(sorted(unknown))}.")


def read_webhook_endpoints(data: dict) -> list[WebhookEndpoint]:
    """The top-level `[[webhook_endpoints]]` array of common_config.toml. Also reads the
    older forms under `[resource_limits]`: an array of tables, or a `{url = token}` table."""
    endpoints = data.get("webhook_endpoints")
    if endpoints is None:
        endpoints = (data.get("resource_limits") or {}).get("webhook_endpoints", [])
    if isinstance(endpoints, dict):
        endpoints = [{"url": url, "token": token} for url, token in endpoints.items()]
    return [WebhookEndpoint(**endpoint) for endpoint in endpoints]


def validate_webhook_endpoints(endpoints: list[WebhookEndpoint]) -> None:
    """Raise when an endpoint is invalid or a URL is used twice."""
    for endpoint in endpoints:
        endpoint.validate()
    urls = [endpoint.url for endpoint in endpoints]
    if len(set(urls)) != len(urls):
        raise ValueError("Each webhook endpoint needs its own URL.")
