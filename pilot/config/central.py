from dataclasses import dataclass


@dataclass
class CentralConfig:
    endpoint: str = ""
    auth_token: str = ""
    bootstrap_token: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "CentralConfig":
        return cls(
            endpoint=data.get("endpoint", ""),
            auth_token=data.get("auth_token", ""),
            bootstrap_token=data.get("bootstrap_token", ""),
        )
