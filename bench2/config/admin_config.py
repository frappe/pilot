from dataclasses import dataclass


@dataclass
class AdminConfig:
    port: int = 8002
    timeout: int = 900  # seconds (15 minutes)
