from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LetsEncryptConfig:
    email: str = ""
    webroot_path: Path = field(default_factory=lambda: Path("/var/www/letsencrypt"))
