import ipaddress
from dataclasses import dataclass, field

from pilot.exceptions import ConfigError


@dataclass
class FirewallRule:
    """One HTTP/HTTPS firewall rule: allow or deny an IPv4/IPv6 address or CIDR."""

    ip: str
    action: str = "deny"  # "allow" | "deny"
    description: str = ""


@dataclass
class FirewallConfig:
    """IP allow/block list applied to every nginx vhost."""

    enabled: bool = False
    default: str = "allow"  # "allow" | "deny"
    rules: list[FirewallRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "FirewallConfig":
        if not data:
            return cls()
        rules = [
            FirewallRule(
                ip=str(rule.get("ip", "")),
                action=str(rule.get("action", "deny")),
                description=str(rule.get("description", "")),
            )
            for rule in data.get("rules", [])
        ]
        return cls(
            enabled=bool(data.get("enabled", False)),
            default=str(data.get("default", "allow")),
            rules=rules,
        )

    def validate(self) -> None:
        if self.default not in ("allow", "deny"):
            raise ConfigError(f"firewall.default '{self.default}' is invalid. Must be 'allow' or 'deny'.")
        for i, rule in enumerate(self.rules):
            prefix = f"firewall.rules[{i}]"
            if rule.action not in ("allow", "deny"):
                raise ConfigError(f"{prefix}.action '{rule.action}' is invalid. Must be 'allow' or 'deny'.")
            try:
                # strict=False accepts a host address with a prefix (e.g. 10.0.0.5/8).
                ipaddress.ip_network(rule.ip, strict=False)
            except ValueError:
                raise ConfigError(f"{prefix}.ip '{rule.ip}' is not a valid IPv4/IPv6 address or CIDR range.")
