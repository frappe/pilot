from dataclasses import dataclass


@dataclass
class UpdatesConfig:
    """Host-wide Pilot update policy."""

    auto_pilot_update: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "UpdatesConfig":
        """The updates config from its TOML table."""
        return cls(auto_pilot_update=bool(data.get("auto_pilot_update", True)))
