import ipaddress
from dataclasses import dataclass

# https://www.cloudflare.com/ips/
CLOUDFLARE_NETWORKS = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)


@dataclass
class ProxyConfig:
    """Host-shared settings for the edge in front of this host."""

    # The edge may stream custom TLS domains with PROXY protocol v2.
    protocol_v2: bool = False
    # Take the client IP from CF-Connecting-IP.
    cloudflare: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        """The proxy config from its TOML table."""
        return cls(
            protocol_v2=bool(data.get("protocol_v2", False)),
            cloudflare=bool(data.get("cloudflare", False)),
        )


def is_cloudflare_address(address: str) -> bool:
    """Whether the address belongs to Cloudflare."""
    ip = ipaddress.ip_address(address)
    return any(ip in ipaddress.ip_network(network) for network in CLOUDFLARE_NETWORKS)
