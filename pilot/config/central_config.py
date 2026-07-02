from dataclasses import dataclass


@dataclass
class CentralConfig:
    endpoint: str = ""
    auth_token: str = ""
