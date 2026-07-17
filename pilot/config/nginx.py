from dataclasses import dataclass
from pathlib import Path


@dataclass
class NginxConfig:
    http_port: int = 80
    https_port: int = 443
    # Empty means "use the platform default" - resolved by whoever consumes
    # this (NginxManager), which already knows how to find it per-OS.
    config_dir: Path = Path("")
    worker_processes: str = "auto"
    client_max_body_size: str = "50m"
