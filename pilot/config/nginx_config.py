from dataclasses import dataclass, field
from pathlib import Path

from pilot.managers.platform import default_nginx_config_dir


@dataclass
class NginxConfig:
    http_port: int = 80
    https_port: int = 443
    config_dir: Path = field(default_factory=default_nginx_config_dir)
    worker_processes: str = "auto"
    client_max_body_size: str = "50m"
