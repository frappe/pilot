from dataclasses import dataclass


@dataclass
class ProxyConfig:
    """Host-shared settings for the edge in front of this host."""

    # The edge may stream custom TLS domains with PROXY protocol v2.
    protocol_v2: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        return cls(protocol_v2=bool(data.get("protocol_v2", False)))
