from dataclasses import dataclass


@dataclass
class AdminConfig:
    port: int = 8002
    timeout: int = 180  # seconds
    enabled: bool = False
    password: str = ""
    domain: str = ""
