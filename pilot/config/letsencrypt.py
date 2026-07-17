import re
from dataclasses import dataclass, field
from pathlib import Path

from pilot.exceptions import ConfigError

_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")


@dataclass
class LetsEncryptConfig:
    email: str = ""
    webroot_path: Path = field(default_factory=lambda: Path("/var/www/letsencrypt"))

    @classmethod
    def from_dict(cls, data: dict) -> "LetsEncryptConfig":
        return cls(
            email=data.get("email", ""),
            webroot_path=Path(data.get("webroot_path", "/var/www/letsencrypt")),
        )

    def validate(self) -> None:
        if self.email and not _EMAIL_PATTERN.match(self.email):
            raise ConfigError(f"letsencrypt.email '{self.email}' is not a valid email address.")
