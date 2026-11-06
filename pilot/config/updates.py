from dataclasses import dataclass


@dataclass
class UpdatesConfig:
    """Host-wide Pilot update policy."""

    auto_pilot_update: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "UpdatesConfig":
        return cls(auto_pilot_update=bool(data.get("auto_pilot_update", True)))
