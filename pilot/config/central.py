from dataclasses import dataclass, field

from pilot.exceptions import ConfigError

UPDATE_CHANNELS = ("early", "normal", "late")


@dataclass
class HostnameAlias:
    type: str
    pattern: str
    target: str
    redirect: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "HostnameAlias":
        return cls(
            type=data.get("type", ""),
            pattern=data.get("pattern", ""),
            target=data.get("target", ""),
            redirect=bool(data.get("redirect", True)),
        )


@dataclass
class CentralConfig:
    """Central-managed state; endpoint and token come from instance metadata."""

    enabled: bool = False
    bootstrapped: bool = False
    hostname_aliases: list[HostnameAlias] = field(default_factory=list)
    update_channel: str = "normal"

    @property
    def is_awaiting_bootstrap(self) -> bool:
        """Central-managed, but the credential has not arrived."""
        return self.enabled and not self.bootstrapped

    def validate(self) -> None:
        if self.update_channel not in UPDATE_CHANNELS:
            raise ConfigError(
                f"central.update_channel must be one of {', '.join(UPDATE_CHANNELS)}, got '{self.update_channel}'."
            )

    @classmethod
    def from_dict(cls, data: dict) -> "CentralConfig":
        return cls(
            enabled=bool(data.get("enabled", False)),
            bootstrapped=bool(data.get("bootstrapped", False)),
            hostname_aliases=[HostnameAlias.from_dict(alias) for alias in data.get("hostname_aliases", [])],
            update_channel=data.get("update_channel", "normal"),
        )
