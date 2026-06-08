from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NginxConfig:
    http_port: int = 80
    https_port: int = 443
    config_dir: Path = field(default_factory=lambda: Path("/etc/nginx/conf.d"))
    worker_processes: str = "auto"
    client_max_body_size: str = "50m"
