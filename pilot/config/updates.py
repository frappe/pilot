from dataclasses import dataclass


@dataclass
class UpdatesConfig:
    """Host-wide Pilot update policy. `auto` installs new releases daily; off by default."""

    auto: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "UpdatesConfig":
        return cls(auto=bool(data.get("auto", False)))
