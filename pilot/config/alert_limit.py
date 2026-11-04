from dataclasses import dataclass, field

from pilot.config.mail import ADDRESS_RE

DEFAULT_USAGE_LIMIT = 90
RESOURCE_LIMIT_FIELDS = ("cpu_usage_limit", "memory_usage_limit", "disk_space_limit")


@dataclass
class ResourceLimitConfig:
    """Usage percentages that raise a resource alert, on by default. Zero disables the alert.
    Alerts go to Central on a managed host, and to the webhooks and mail recipients set here."""

    cpu_usage_limit: int = DEFAULT_USAGE_LIMIT
    memory_usage_limit: int = DEFAULT_USAGE_LIMIT
    disk_space_limit: int = DEFAULT_USAGE_LIMIT
    site_uptime: bool = True
    email_recipients: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for name in RESOURCE_LIMIT_FIELDS:
            limit = getattr(self, name)
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise ValueError(f"resource_limits.{name} must be a whole number.")
            if not 0 <= limit <= 100:
                raise ValueError(f"resource_limits.{name} must be a percentage between 0 and 100.")

        for recipient in self.email_recipients:
            # A stricter check than "has an @": a space or newline here would end up
            # in a To: header, where it splits the header rather than failing cleanly.
            if not ADDRESS_RE.match(recipient):
                raise ValueError(f"resource_limits.email_recipients has a bad address: {recipient!r}.")
